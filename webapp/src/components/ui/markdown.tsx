import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** Agent prose: GitHub-flavoured markdown styled with the tokens (see `.prose-md` in index.css). */
export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn("prose-md break-words", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ ...p }) => <a {...p} target="_blank" rel="noreferrer" /> }}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
