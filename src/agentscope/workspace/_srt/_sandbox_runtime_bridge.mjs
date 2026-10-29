import { pathToFileURL } from "node:url";
import { spawn } from "node:child_process";

let activeChild = null;

function terminateActiveChild() {
  if (activeChild && !activeChild.killed) {
    activeChild.kill("SIGKILL");
  }
}

process.on("SIGTERM", () => {
  terminateActiveChild();
  process.exit(1);
});

process.on("SIGINT", () => {
  terminateActiveChild();
  process.exit(1);
});

async function readStdinJson() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw ? JSON.parse(raw) : {};
}

function waitForExit(child, timeoutMs) {
  return new Promise((resolve, reject) => {
    let finished = false;
    let timeout = null;
    if (timeoutMs && timeoutMs > 0) {
      timeout = setTimeout(() => {
        if (finished) return;
        finished = true;
        child.kill("SIGKILL");
        resolve({ timedOut: true, code: -1 });
      }, timeoutMs);
    }

    child.on("error", error => {
      if (finished) return;
      finished = true;
      if (timeout) clearTimeout(timeout);
      reject(error);
    });

    child.on("close", code => {
      if (finished) return;
      finished = true;
      if (timeout) clearTimeout(timeout);
      resolve({ timedOut: false, code: code ?? 0 });
    });
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function settleViolations(
  store,
  commandId,
  {
    maxWaitMs = 250,
    minQuietMs = 60,
    minWaitMs = 30,
    initialPollMs = 10,
    maxPollMs = 40,
  } = {},
) {
  const startedAt = Date.now();
  let quietSince = startedAt;
  let pollMs = initialPollMs;
  let previousCount = store.getViolationsForCommand(commandId).length;

  while (Date.now() - startedAt < maxWaitMs) {
    await sleep(pollMs);

    const current = store.getViolationsForCommand(commandId);
    const currentCount = current.length;
    const now = Date.now();
    if (currentCount !== previousCount) {
      previousCount = currentCount;
      quietSince = now;
      pollMs = initialPollMs;
      continue;
    }

    if (now - startedAt >= minWaitMs && now - quietSince >= minQuietMs) {
      return current;
    }
    pollMs = Math.min(pollMs * 2, maxPollMs);
  }
  return store.getViolationsForCommand(commandId);
}

async function main() {
  const payload = await readStdinJson();
  const moduleUrl = pathToFileURL(payload.sandboxRuntimeModulePath).href;
  const { SandboxManager } = await import(moduleUrl);

  await SandboxManager.initialize(payload.runtimeConfig, undefined, true);
  try {
    const wrapped = await SandboxManager.wrapWithSandboxArgv(
      payload.command,
      undefined,
      undefined,
      undefined,
      payload.cwd ?? undefined,
      {
        commandId: payload.commandId,
        commandText: payload.commandText ?? payload.command,
      },
    );

      const child = spawn(wrapped.argv[0], wrapped.argv.slice(1), {
      cwd: payload.cwd ?? undefined,
      env: wrapped.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
      activeChild = child;

    const stderrChunks = [];
    child.stderr.on("data", chunk => stderrChunks.push(chunk));

      if (payload.streamStdout) {
        child.stdout.on("data", chunk => {
          if (!process.stdout.write(chunk)) {
            child.stdout.pause();
          }
        });
        process.stdout.on("drain", () => {
          child.stdout.resume();
        });
      } else {
        const stdoutChunks = [];
        child.stdout.on("data", chunk => stdoutChunks.push(chunk));

        const { timedOut, code } = await waitForExit(child, payload.timeoutMs ?? null);
        activeChild = null;
        const stdout = Buffer.concat(stdoutChunks);
        const stderrBuffer = Buffer.concat(stderrChunks);
        const stderrText = stderrBuffer.toString("utf8");
        const store = SandboxManager.getSandboxViolationStore();
        const violations = await settleViolations(store, payload.commandId);
        const annotatedStderr = SandboxManager.annotateStderrWithSandboxFailures(
          payload.commandId,
          stderrText,
        );

        process.stdout.write(
          JSON.stringify({
            exit_code: timedOut ? -1 : code,
            timed_out: timedOut,
            stdout: stdout.toString("base64"),
            stderr: stderrBuffer.toString("base64"),
            annotated_stderr: Buffer.from(annotatedStderr, "utf8").toString("base64"),
            violations: violations.map(item => item.line),
          }),
        );
        return;
      }

    const { timedOut, code } = await waitForExit(child, payload.timeoutMs ?? null);
      activeChild = null;
    const stderrBuffer = Buffer.concat(stderrChunks);
    const stderrText = stderrBuffer.toString("utf8");
    const store = SandboxManager.getSandboxViolationStore();
    const violations = await settleViolations(store, payload.commandId);
    const annotatedStderr = SandboxManager.annotateStderrWithSandboxFailures(
      payload.commandId,
      stderrText,
    );

      process.stderr.write(
      JSON.stringify({
        exit_code: timedOut ? -1 : code,
        timed_out: timedOut,
        stderr: stderrBuffer.toString("base64"),
        annotated_stderr: Buffer.from(annotatedStderr, "utf8").toString("base64"),
        violations: violations.map(item => item.line),
      }),
    );
  } finally {
    await SandboxManager.reset();
  }
}

main().catch(error => {
  process.stderr.write(
    JSON.stringify({
      error_type: error?.name ?? "Error",
      message: error?.message ?? String(error),
    }),
  );
  process.exit(1);
});
