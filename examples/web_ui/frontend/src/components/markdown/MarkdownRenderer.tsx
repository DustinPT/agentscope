import { Copy } from 'lucide-react';
import { Children, isValidElement, useMemo, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components, UrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { MermaidChartCard } from '@/components/markdown/MermaidChartCard';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface MarkdownRendererProps {
        children: string;
        components?: Components;
        urlTransform?: UrlTransform;
        className?: string;
}

function hasMermaidCard(node: ReactNode): boolean {
        return Children.toArray(node).some((child) => {
                if (!isValidElement(child)) {
                        return false;
                }

                const childProps = child.props as {
                        children?: ReactNode;
                        'data-mermaid-card'?: unknown;
                        className?: string;
                        node?: {
                                properties?: {
                                        className?: string | string[];
                                };
                        };
                };
                const nodeClassName = childProps.node?.properties?.className;
                const mergedClassName = [
                        childProps.className,
                        Array.isArray(nodeClassName) ? nodeClassName.join(' ') : nodeClassName,
                ]
                        .filter(Boolean)
                        .join(' ');

                if (child.type === MermaidChartCard) {
                        return true;
                }

                if (childProps['data-mermaid-card']) {
                        return true;
                }

                if (mergedClassName.includes('language-mermaid')) {
                        return true;
                }

                return hasMermaidCard(childProps.children);
        });
}

/**
 * Shared markdown renderer used across chat and file preview surfaces.
 */
export function MarkdownRenderer({
        children,
        components,
        urlTransform,
        className,
}: MarkdownRendererProps) {
        const mergedComponents = useMemo<Components>(
                () => ({
                        pre: ({ children: preChildren, ...props }) => {
                                if (hasMermaidCard(preChildren)) {
                                        return <>{preChildren}</>;
                                }

                                return <pre {...props}>{preChildren}</pre>;
                        },
                        code: ({ className: codeClassName, children: codeChildren, ...props }) => {
                                const language = String(codeClassName ?? '').replace('language-', '').trim();
                                const content = String(codeChildren ?? '').replace(/\n$/, '');
                                const isInline = !language;

                                if (language === 'mermaid') {
                                        return <MermaidChartCard chart={content} />;
                                }

                                if (isInline) {
                                        return (
                                                <code
                                                        className={cn(codeClassName, 'break-all')}
                                                        {...props}
                                                >
                                                        {codeChildren}
                                                </code>
                                        );
                                }

                                return (
                                        <div className="relative w-full">
                                                <Button
                                                        size="icon-xs"
                                                        variant="ghost"
                                                        className="absolute top-0 right-0 z-10"
                                                        onClick={async (event) => {
                                                                event.preventDefault();
                                                                event.stopPropagation();
                                                                await navigator.clipboard.writeText(content);
                                                        }}
                                                >
                                                        <Copy />
                                                </Button>
                                                <div className="overflow-x-auto max-w-full w-full">
                                                        <code className={codeClassName} {...props}>
                                                                {codeChildren}
                                                        </code>
                                                </div>
                                        </div>
                                );
                        },
                        ...components,
                }),
                [components],
        );

        return (
                <div className={className}>
                        <ReactMarkdown
                                remarkPlugins={[remarkGfm]}
                                urlTransform={urlTransform}
                                components={mergedComponents}
                        >
                                {children}
                        </ReactMarkdown>
                </div>
        );
}
