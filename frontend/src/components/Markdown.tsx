import { useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Check, Copy } from 'lucide-react';
import 'highlight.js/styles/github-dark.css';

// Recursively pull plain text out of rendered children (for the copy button).
function extractText(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (typeof node === 'object' && 'props' in (node as any)) {
    return extractText((node as any).props?.children);
  }
  return '';
}

function CodeBlock({ language, children }: { language?: string; children: ReactNode }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(extractText(children).replace(/\n$/, ''));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <div className="my-3 rounded-lg overflow-hidden border border-border bg-[#0d1117]">
      <div className="flex items-center justify-between px-3 py-1.5 bg-[#161b22] border-b border-border">
        <span className="text-[11px] font-mono text-[#8b949e] lowercase">{language || 'code'}</span>
        <button
          onClick={copy}
          className="flex items-center gap-1 text-[11px] text-[#8b949e] hover:text-[#e6edf3] transition-colors"
        >
          {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="px-4 py-3 overflow-x-auto text-[12.5px] leading-relaxed">
        <code className={`hljs language-${language || 'plaintext'} bg-transparent`}>{children}</code>
      </pre>
    </div>
  );
}

interface MarkdownProps {
  children: string;
  className?: string;
}

/**
 * Renders chat content as rich markdown — GitHub-flavored (tables, task lists,
 * strikethrough) with syntax-highlighted, copyable code blocks. Raw HTML is NOT
 * rendered (no rehype-raw), so model output can't inject markup.
 */
export function Markdown({ children, className = '' }: MarkdownProps) {
  return (
    <div className={`vigilus-markdown text-[14px] leading-relaxed ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          code({ className: cls, children: kids }) {
            const match = /language-(\w+)/.exec(cls || '');
            const text = extractText(kids);
            const isBlock = !!match || text.includes('\n');
            if (isBlock) {
              return <CodeBlock language={match?.[1]}>{kids}</CodeBlock>;
            }
            return (
              <code className="px-1.5 py-0.5 mx-0.5 rounded bg-black/[0.06] dark:bg-white/10 text-[0.9em] font-mono text-accent dark:text-accent break-words">
                {kids}
              </code>
            );
          },
          a({ children: kids, href }) {
            return (
              <a href={href} target="_blank" rel="noreferrer noopener"
                 className="text-accent dark:text-accent underline underline-offset-2 hover:opacity-80 break-words">
                {kids}
              </a>
            );
          },
          p({ children: kids }) {
            return <p className="my-2 first:mt-0 last:mb-0 break-words">{kids}</p>;
          },
          ul({ children: kids }) {
            return <ul className="my-2 ml-5 list-disc space-y-1 marker:text-text-secondary">{kids}</ul>;
          },
          ol({ children: kids }) {
            return <ol className="my-2 ml-5 list-decimal space-y-1 marker:text-text-secondary">{kids}</ol>;
          },
          li({ children: kids }) {
            return <li className="break-words">{kids}</li>;
          },
          h1: ({ children: kids }) => <h1 className="text-[18px] font-semibold mt-4 mb-2 first:mt-0">{kids}</h1>,
          h2: ({ children: kids }) => <h2 className="text-[16px] font-semibold mt-4 mb-2 first:mt-0">{kids}</h2>,
          h3: ({ children: kids }) => <h3 className="text-[15px] font-semibold mt-3 mb-1.5 first:mt-0">{kids}</h3>,
          blockquote: ({ children: kids }) => (
            <blockquote className="my-2 pl-3 border-l-2 border-accent/40 text-text-secondary italic">{kids}</blockquote>
          ),
          hr: () => <hr className="my-4 border-border dark:border-border" />,
          table: ({ children: kids }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full text-[13px] border-collapse">{kids}</table>
            </div>
          ),
          thead: ({ children: kids }) => <thead className="bg-surface dark:bg-surface">{kids}</thead>,
          th: ({ children: kids }) => (
            <th className="border border-border dark:border-border px-3 py-1.5 text-left font-medium">{kids}</th>
          ),
          td: ({ children: kids }) => (
            <td className="border border-border dark:border-border px-3 py-1.5 align-top">{kids}</td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
