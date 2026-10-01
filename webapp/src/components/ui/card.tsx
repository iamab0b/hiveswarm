import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("card", className)} {...props} />;
}

/** A 36 px row at the top of a card: title on the left, actions on the right. */
export function CardHeader({ className, title, count, right, children, ...props }: React.HTMLAttributes<HTMLDivElement> & { title?: React.ReactNode; count?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className={cn("flex min-h-9 items-center gap-2 border-b border-border px-3", className)} {...props}>
      {title ? <div className="text-label text-fg">{title}</div> : null}
      {count !== undefined && count !== null && count !== "" ? <span className="num text-meta">{count}</span> : null}
      {children}
      {right ? <div className="ml-auto flex items-center gap-1">{right}</div> : null}
    </div>
  );
}

export function CardSection({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-4 py-3", className)} {...props} />;
}

/** A labelled block of a page: a small label, then content. */
export function Section({ label, right, className, children }: { label: React.ReactNode; right?: React.ReactNode; className?: string; children: React.ReactNode }) {
  return (
    <section className={className}>
      <div className="mb-2 flex items-center justify-between">
        <div className="text-label">{label}</div>
        {right}
      </div>
      {children}
    </section>
  );
}
