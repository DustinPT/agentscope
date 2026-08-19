import {
        Copy,
        Download,
        Expand,
        Maximize,
        Minus,
        Plus,
        ScanSearch,
        Shrink,
} from 'lucide-react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
        DropdownMenu,
        DropdownMenuContent,
        DropdownMenuItem,
        DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface MermaidChartCardProps {
        chart: string;
}

interface SvgDimensions {
        width: number;
        height: number;
}

interface PanPoint {
        x: number;
        y: number;
}

interface MermaidRenderOptions {
        htmlLabels?: boolean;
}

const ZOOM_FACTORS = [0.75, 0.9, 1, 1.1, 1.25, 1.5, 2] as const;
const DEFAULT_ZOOM_FACTOR = 1;
const MIN_ZOOM_FACTOR = ZOOM_FACTORS[0];
const MAX_ABSOLUTE_SCALE = 2;
const ZOOM_STEP_MULTIPLIER = 1.25;
const CARD_VIEWPORT_HEIGHT = 350;

async function renderMermaidSvg(
        mermaidSource: string,
        diagramId: string,
        theme: 'default' | 'dark',
        options?: MermaidRenderOptions,
): Promise<string> {
        const htmlLabels = options?.htmlLabels ?? false;
        const mermaid = (await import('mermaid')).default;
        mermaid.initialize({
                startOnLoad: false,
                securityLevel: 'antiscript',
                theme,
                htmlLabels,
                flowchart: {
                        htmlLabels,
                },
        });
        const { svg } = await mermaid.render(`mermaid-${diagramId}`, mermaidSource);
        return svg;
}

function parseNumericDimension(value?: string | null): number | null {
        if (!value || value.includes('%')) return null;
        const parsed = Number.parseFloat(value);
        return Number.isFinite(parsed) ? parsed : null;
}

function getSvgDimensions(svg: string): SvgDimensions | null {
        if (typeof DOMParser === 'undefined') return null;

        const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
        const element = document.documentElement;
        if (element.tagName.toLowerCase() !== 'svg') return null;

        const viewBox = element.getAttribute('viewBox');
        if (viewBox) {
                const parts = viewBox
                        .trim()
                        .split(/[\s,]+/)
                        .map((part) => Number.parseFloat(part));
                if (parts.length === 4 && parts.every((part) => Number.isFinite(part))) {
                        const width = Math.abs(parts[2]);
                        const height = Math.abs(parts[3]);
                        if (width > 0 && height > 0) {
                                return { width, height };
                        }
                }
        }

        const width = parseNumericDimension(element.getAttribute('width'));
        const height = parseNumericDimension(element.getAttribute('height'));
        if (width && height && width > 0 && height > 0) {
                return { width, height };
        }

        return null;
}

function clampPan(pan: PanPoint, scaled: SvgDimensions, viewport: SvgDimensions): PanPoint {
        const maxX = Math.max(0, (scaled.width - viewport.width) / 2);
        const maxY = Math.max(0, (scaled.height - viewport.height) / 2);

        return {
                x: maxX === 0 ? 0 : Math.min(maxX, Math.max(-maxX, pan.x)),
                y: maxY === 0 ? 0 : Math.min(maxY, Math.max(-maxY, pan.y)),
        };
}

function downloadBlob(blob: Blob, filename: string) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
}

function loadImage(url: string): Promise<HTMLImageElement> {
        return new Promise((resolve, reject) => {
                const image = new Image();
                image.onload = () => resolve(image);
                image.onerror = () => reject(new Error('Failed to load SVG image.'));
                image.src = url;
        });
}

/**
 * Rich Mermaid chart card used by shared markdown rendering.
 */
export function MermaidChartCard({ chart }: MermaidChartCardProps) {
        const { t } = useTranslation();
        const diagramId = useId().replace(/:/g, '-');
        const rootRef = useRef<HTMLDivElement | null>(null);
        const dragStartRef = useRef<{
                pointerId: number;
                origin: PanPoint;
                pointer: PanPoint;
        } | null>(null);

        const normalizedChart = useMemo(() => chart.trim(), [chart]);
        const [activeTab, setActiveTab] = useState<'diagram' | 'code'>('diagram');
        const [svg, setSvg] = useState<string | null>(null);
        const [hasError, setHasError] = useState(false);
        const [isRendering, setIsRendering] = useState(false);
        const [isFullscreen, setIsFullscreen] = useState(false);
        const [isDragging, setIsDragging] = useState(false);
        const [zoomFactor, setZoomFactor] = useState(DEFAULT_ZOOM_FACTOR);
        const [pan, setPan] = useState<PanPoint>({ x: 0, y: 0 });
        const [viewportSize, setViewportSize] = useState<SvgDimensions>({
                width: 0,
                height: CARD_VIEWPORT_HEIGHT,
        });
        const [viewportElement, setViewportElement] = useState<HTMLDivElement | null>(null);
        const [theme, setTheme] = useState<'default' | 'dark'>(() =>
                document.documentElement.classList.contains('dark') ? 'dark' : 'default',
        );

        const svgDimensions = useMemo(() => (svg ? getSvgDimensions(svg) : null), [svg]);
        const svgDataUrl = useMemo(() => {
                if (!svg) return null;
                return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
        }, [svg]);
        const fitScale = useMemo(() => {
                if (!svgDimensions || viewportSize.width <= 0 || viewportSize.height <= 0) {
                        return 1;
                }

                const widthScale = viewportSize.width / svgDimensions.width;
                const heightScale = viewportSize.height / svgDimensions.height;
                return Math.min(1, widthScale, heightScale);
        }, [svgDimensions, viewportSize.height, viewportSize.width]);
        const maxZoomFactor = useMemo(() => {
                if (fitScale <= 0) {
                        return 2;
                }
                return Math.max(2, MAX_ABSOLUTE_SCALE / fitScale);
        }, [fitScale]);
        const displayScale = fitScale * zoomFactor;
        const scaledSize = useMemo(
                () => ({
                        width: (svgDimensions?.width ?? 0) * displayScale,
                        height: (svgDimensions?.height ?? 0) * displayScale,
                }),
                [displayScale, svgDimensions],
        );
        const clampedPan = useMemo(
                () => clampPan(pan, scaledSize, viewportSize),
                [pan, scaledSize, viewportSize],
        );

        useEffect(() => {
                const root = document.documentElement;
                const observer = new MutationObserver(() => {
                        setTheme(root.classList.contains('dark') ? 'dark' : 'default');
                });

                observer.observe(root, {
                        attributes: true,
                        attributeFilter: ['class'],
                });

                return () => observer.disconnect();
        }, []);

        useEffect(() => {
                const handleFullscreenChange = () => {
                        setIsFullscreen(document.fullscreenElement === rootRef.current);
                };

                document.addEventListener('fullscreenchange', handleFullscreenChange);
                handleFullscreenChange();

                return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
        }, []);

        useEffect(() => {
                const element = viewportElement;
                if (!element) return;

                const observer = new ResizeObserver((entries) => {
                        const entry = entries[0];
                        if (!entry) return;
                        if (entry.contentRect.width <= 0 || entry.contentRect.height <= 0) {
                                return;
                        }
                        setViewportSize({
                                width: entry.contentRect.width,
                                height: entry.contentRect.height,
                        });
                });

                observer.observe(element);
                return () => observer.disconnect();
        }, [viewportElement]);

        useEffect(() => {
                let cancelled = false;

                async function renderDiagram() {
                        try {
                                setIsRendering(true);
                                const renderedSvg = await renderMermaidSvg(
                                        normalizedChart,
                                        diagramId,
                                        theme,
                                );

                                if (!cancelled) {
                                        setSvg(renderedSvg);
                                        setHasError(false);
                                        setPan({ x: 0, y: 0 });
                                        setZoomFactor(DEFAULT_ZOOM_FACTOR);
                                }
                        } catch (error) {
                                console.error('Failed to render mermaid diagram.', error);
                                if (!cancelled) {
                                        setSvg(null);
                                        setHasError(true);
                                        setPan({ x: 0, y: 0 });
                                        setZoomFactor(DEFAULT_ZOOM_FACTOR);
                                }
                        } finally {
                                if (!cancelled) {
                                        setIsRendering(false);
                                }
                        }
                }

                if (!normalizedChart) {
                        setSvg(null);
                        setHasError(false);
                        setIsRendering(false);
                        return;
                }

                void renderDiagram();

                return () => {
                        cancelled = true;
                };
        }, [diagramId, normalizedChart, theme]);

        useEffect(() => {
                if (pan.x !== clampedPan.x || pan.y !== clampedPan.y) {
                        setPan(clampedPan);
                }
        }, [clampedPan, pan.x, pan.y]);

        const canZoomOut = zoomFactor > MIN_ZOOM_FACTOR + Number.EPSILON;
        const canZoomIn = zoomFactor < maxZoomFactor - Number.EPSILON;
        const fullscreenSupported =
                typeof document !== 'undefined' &&
                typeof document.fullscreenEnabled === 'boolean' &&
                document.fullscreenEnabled;
        const canResetViewport = !!svg && !hasError;

        const handleCopySource = async () => {
                await navigator.clipboard.writeText(normalizedChart);
        };

        const handleFitView = () => {
                setZoomFactor(DEFAULT_ZOOM_FACTOR);
                setPan({ x: 0, y: 0 });
        };

        const handleActualSize = () => {
                if (fitScale <= 0) return;
                setZoomFactor(Math.min(maxZoomFactor, 1 / fitScale));
                setPan({ x: 0, y: 0 });
        };

        const handleDownloadSvg = async () => {
                const exportSvg = await renderMermaidSvg(normalizedChart, `${diagramId}-svg`, theme, {
                        htmlLabels: false,
                });
                downloadBlob(
                        new Blob([exportSvg], { type: 'image/svg+xml;charset=utf-8' }),
                        'mermaid-diagram.svg',
                );
        };

        const handleDownloadSource = () => {
                downloadBlob(
                        new Blob([normalizedChart], { type: 'text/plain;charset=utf-8' }),
                        'mermaid-diagram.mmd',
                );
        };

        const handleDownloadPng = async () => {
                if (!svgDimensions) return;

                // Re-render with HTML labels disabled so the export SVG does not contain
                // foreignObject nodes that taint the canvas during PNG conversion.
                const exportSvg = await renderMermaidSvg(normalizedChart, `${diagramId}-png`, theme, {
                        htmlLabels: false,
                });
                const blob = new Blob([exportSvg], { type: 'image/svg+xml;charset=utf-8' });
                const url = URL.createObjectURL(blob);

                try {
                        const image = await loadImage(url);
                        const canvas = document.createElement('canvas');
                        canvas.width = Math.max(1, Math.round(svgDimensions.width));
                        canvas.height = Math.max(1, Math.round(svgDimensions.height));

                        const context = canvas.getContext('2d');
                        if (!context) {
                                throw new Error('Failed to create canvas context.');
                        }

                        context.fillStyle = '#ffffff';
                        context.fillRect(0, 0, canvas.width, canvas.height);
                        context.drawImage(image, 0, 0, canvas.width, canvas.height);

                        const pngBlob = await new Promise<Blob>((resolve, reject) => {
                                canvas.toBlob((value) => {
                                        if (value) {
                                                resolve(value);
                                        } else {
                                                reject(new Error('Failed to export PNG.'));
                                        }
                                }, 'image/png');
                        });

                        downloadBlob(pngBlob, 'mermaid-diagram.png');
                } finally {
                        URL.revokeObjectURL(url);
                }
        };

        const handleToggleFullscreen = async () => {
                if (!rootRef.current || !fullscreenSupported) return;

                if (document.fullscreenElement === rootRef.current) {
                        await document.exitFullscreen();
                        return;
                }

                await rootRef.current.requestFullscreen();
        };

        const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
                if (event.button !== 0 || activeTab !== 'diagram') return;

                dragStartRef.current = {
                        pointerId: event.pointerId,
                        origin: clampedPan,
                        pointer: { x: event.clientX, y: event.clientY },
                };
                event.currentTarget.setPointerCapture(event.pointerId);
                setIsDragging(true);
        };

        const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
                const dragStart = dragStartRef.current;
                if (!dragStart || dragStart.pointerId !== event.pointerId) return;

                const nextPan = clampPan(
                        {
                                x: dragStart.origin.x + event.clientX - dragStart.pointer.x,
                                y: dragStart.origin.y + event.clientY - dragStart.pointer.y,
                        },
                        scaledSize,
                        viewportSize,
                );
                setPan(nextPan);
        };

        const handlePointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
                if (dragStartRef.current?.pointerId !== event.pointerId) return;

                dragStartRef.current = null;
                setIsDragging(false);
                if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                        event.currentTarget.releasePointerCapture(event.pointerId);
                }
        };

        return (
                <div
                        ref={rootRef}
                        data-mermaid-card="true"
                        className={cn(
                                'mermaid-chart-card not-prose rounded-xl border bg-background',
                                isFullscreen && 'mermaid-chart-card--fullscreen',
                        )}
                >
                        <Tabs
                                value={activeTab}
                                onValueChange={(value) => {
                                        if (value === 'diagram' || value === 'code') {
                                                setActiveTab(value);
                                        }
                                }}
                                className="gap-0"
                        >
                                <div className="mermaid-chart-card__header flex items-center justify-between gap-3 border-b px-4 py-3">
                                        <TabsList className="h-10 bg-muted/80 p-1">
                                                <TabsTrigger value="diagram" className="px-4">
                                                        {t('markdown.mermaid.diagramTab')}
                                                </TabsTrigger>
                                                <TabsTrigger value="code" className="px-4">
                                                        {t('markdown.mermaid.codeTab')}
                                                </TabsTrigger>
                                        </TabsList>

                                        <div className="flex items-center gap-1.5">
                                                {activeTab === 'diagram' ? (
                                                        <>
                                                                <Button
                                                                        type="button"
                                                                        size="icon-sm"
                                                                        variant="ghost"
                                                                        className="text-foreground/80 hover:text-foreground"
                                                                        tooltip={t('markdown.mermaid.fit')}
                                                                        disabled={!canResetViewport}
                                                                        onClick={handleFitView}
                                                                >
                                                                        <ScanSearch className="h-4 w-4" />
                                                                </Button>
                                                                <Button
                                                                        type="button"
                                                                        size="icon-sm"
                                                                        variant="ghost"
                                                                        className="text-foreground/80 hover:text-foreground"
                                                                        tooltip={t('markdown.mermaid.actualSize')}
                                                                        disabled={!canResetViewport}
                                                                        onClick={handleActualSize}
                                                                >
                                                                        <Maximize className="h-4 w-4" />
                                                                </Button>
                                                                <Button
                                                                        type="button"
                                                                        size="icon-sm"
                                                                        variant="ghost"
                                                                        className="text-foreground/80 hover:text-foreground"
                                                                        tooltip={t('markdown.mermaid.zoomOut')}
                                                                        disabled={!canZoomOut || hasError || !svg}
                                                                        onClick={() =>
                                                                                setZoomFactor((current) =>
                                                                                        Math.max(
                                                                                                MIN_ZOOM_FACTOR,
                                                                                                current / ZOOM_STEP_MULTIPLIER,
                                                                                        ),
                                                                                )
                                                                        }
                                                                >
                                                                        <Minus />
                                                                </Button>
                                                                <Button
                                                                        type="button"
                                                                        size="icon-sm"
                                                                        variant="ghost"
                                                                        className="text-foreground/80 hover:text-foreground"
                                                                        tooltip={t('markdown.mermaid.zoomIn')}
                                                                        disabled={!canZoomIn || hasError || !svg}
                                                                        onClick={() =>
                                                                                setZoomFactor((current) =>
                                                                                        Math.min(
                                                                                                maxZoomFactor,
                                                                                                current * ZOOM_STEP_MULTIPLIER,
                                                                                        ),
                                                                                )
                                                                        }
                                                                >
                                                                        <Plus />
                                                                </Button>
                                                        </>
                                                ) : null}
                                                <DropdownMenu>
                                                        <DropdownMenuTrigger asChild>
                                                                <Button
                                                                        type="button"
                                                                        size="icon-sm"
                                                                        variant="ghost"
                                                                        className="text-foreground/80 hover:text-foreground"
                                                                        tooltip={t('markdown.mermaid.download')}
                                                                        disabled={!svg}
                                                                >
                                                                        <Download />
                                                                </Button>
                                                        </DropdownMenuTrigger>
                                                        <DropdownMenuContent align="end" className="w-40 min-w-40">
                                                                <DropdownMenuItem onClick={() => void handleDownloadSvg()}>
                                                                        {t('markdown.mermaid.downloadSvg')}
                                                                </DropdownMenuItem>
                                                                <DropdownMenuItem onClick={() => void handleDownloadPng()}>
                                                                        {t('markdown.mermaid.downloadPng')}
                                                                </DropdownMenuItem>
                                                                <DropdownMenuItem onClick={handleDownloadSource}>
                                                                        {t('markdown.mermaid.downloadSource')}
                                                                </DropdownMenuItem>
                                                        </DropdownMenuContent>
                                                </DropdownMenu>
                                                <Button
                                                        type="button"
                                                        size="icon-sm"
                                                        variant="ghost"
                                                        className="text-foreground/80 hover:text-foreground"
                                                        tooltip={t(
                                                                isFullscreen
                                                                        ? 'markdown.mermaid.exitFullscreen'
                                                                        : 'markdown.mermaid.fullscreen',
                                                        )}
                                                        disabled={!fullscreenSupported}
                                                        onClick={() => void handleToggleFullscreen()}
                                                >
                                                        {isFullscreen ? <Shrink /> : <Expand />}
                                                </Button>
                                        </div>
                                </div>

                                <TabsContent value="diagram" className="mt-0">
                                        <div
                                                ref={setViewportElement}
                                                className={cn(
                                                        'mermaid-diagram mermaid-chart-card__viewport relative overflow-hidden rounded-b-xl bg-muted/35',
                                                        isDragging ? 'cursor-grabbing' : 'cursor-grab',
                                                        activeTab !== 'diagram' && 'cursor-default',
                                                )}
                                                style={{
                                                        height: isFullscreen
                                                                ? 'calc(100vh - 120px)'
                                                                : `${CARD_VIEWPORT_HEIGHT}px`,
                                                }}
                                                onPointerDown={handlePointerDown}
                                                onPointerMove={handlePointerMove}
                                                onPointerUp={handlePointerEnd}
                                                onPointerCancel={handlePointerEnd}
                                        >
                                                {isRendering ? (
                                                        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                                                                {t('markdown.mermaid.rendering')}
                                                        </div>
                                                ) : hasError || !svg || !svgDimensions || !svgDataUrl ? (
                                                        <div className="flex h-full flex-col gap-3 p-4">
                                                                <div className="rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                                                                        {t('markdown.mermaid.renderFailed')}
                                                                </div>
                                                                <pre className="flex-1 overflow-auto rounded-lg border bg-background p-4 text-xs whitespace-pre-wrap break-words">
                                                                        <code>{normalizedChart}</code>
                                                                </pre>
                                                        </div>
                                                ) : (
                                                        <>
                                                                <div
                                                                        className="mermaid-chart-card__canvas absolute inset-0 touch-none select-none"
                                                                        aria-label={t('markdown.mermaid.dragHint')}
                                                                >
                                                                        <div
                                                                                className="mermaid-chart-card__svg absolute"
                                                                                style={{
                                                                                        width: `${svgDimensions.width}px`,
                                                                                        height: `${svgDimensions.height}px`,
                                                                                        left: `${(viewportSize.width - scaledSize.width) / 2 + clampedPan.x}px`,
                                                                                        top: `${(viewportSize.height - scaledSize.height) / 2 + clampedPan.y}px`,
                                                                                        transform: `scale(${displayScale})`,
                                                                                        transformOrigin: 'top left',
                                                                                }}
                                                                        >
                                                                                <img
                                                                                        src={svgDataUrl}
                                                                                        alt="Mermaid diagram"
                                                                                        draggable={false}
                                                                                        className="pointer-events-none block h-full w-full select-none"
                                                                                />
                                                                        </div>
                                                                </div>
                                                                <div className="pointer-events-none absolute right-4 bottom-4 left-4 flex items-center justify-between text-xs text-muted-foreground">
                                                                        <span>{t('markdown.mermaid.dragHint')}</span>
                                                                        <span>{Math.round(displayScale * 100)}%</span>
                                                                </div>
                                                        </>
                                                )}
                                        </div>
                                </TabsContent>

                                <TabsContent value="code" className="mt-0">
                                        <div
                                                className="relative rounded-b-xl bg-muted/20"
                                                style={{
                                                        height: isFullscreen
                                                                ? 'calc(100vh - 120px)'
                                                                : `${CARD_VIEWPORT_HEIGHT}px`,
                                                }}
                                        >
                                                <Button
                                                        type="button"
                                                        size="icon-xs"
                                                        variant="ghost"
                                                        className="absolute top-3 right-3 z-10 text-foreground/80 hover:text-foreground"
                                                        tooltip={t('workspace-drawer.file.copy')}
                                                        onClick={() => void handleCopySource()}
                                                >
                                                        <Copy />
                                                </Button>
                                                <pre className="h-full overflow-auto rounded-b-xl bg-background p-4 pr-12 font-mono text-sm leading-6 text-foreground whitespace-pre-wrap break-words">
                                                        <code>{normalizedChart}</code>
                                                </pre>
                                        </div>
                                </TabsContent>
                        </Tabs>
                </div>
        );
}
