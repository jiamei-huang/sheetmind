import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { normalizeAnalysisMarkdown } from "../../utils/analysisMarkdown";

const COMPONENTS = {
  h1: ({ children }) => <h3 className="mb-1.5 mt-4 text-base font-semibold text-slate-950 first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mb-1.5 mt-4 text-base font-semibold text-slate-950 first:mt-0">{children}</h3>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-4 text-base font-semibold text-slate-950 first:mt-0">{children}</h3>,
  p: ({ children }) => <p className="my-2 whitespace-pre-wrap break-words first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-slate-950">{children}</strong>,
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-slate-300 pl-3 text-slate-600">{children}</blockquote>
  ),
  code: ({ children }) => (
    <code className="rounded bg-slate-100 px-1 py-0.5 text-[0.9em] text-slate-800">{children}</code>
  ),
  table: ({ children }) => (
    <table className="my-3 block max-w-full overflow-x-auto border-collapse text-sm">{children}</table>
  ),
  th: ({ children }) => <th className="border border-slate-200 bg-slate-50 px-2 py-1.5 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-slate-200 px-2 py-1.5">{children}</td>,
};

const AnalysisMarkdown = ({ children, className = "" }) => (
  <div className={`text-[15px] leading-6 text-slate-700 ${className}`}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
      {normalizeAnalysisMarkdown(children)}
    </ReactMarkdown>
  </div>
);

export default AnalysisMarkdown;
