import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors focus-ring disabled:pointer-events-none disabled:opacity-50 select-none",
  {
    variants: {
      variant: {
        default: "bg-fg text-bg hover:opacity-90",
        accent: "bg-accent text-accent-fg hover:brightness-110",
        secondary: "bg-surface-2 text-fg hover:bg-surface-3 border border-border",
        ghost: "text-muted hover:text-fg hover:bg-surface-2",
        outline: "border border-border-strong text-fg hover:bg-surface-2",
        danger: "bg-danger/15 text-danger border border-danger/30 hover:bg-danger/25",
        success: "bg-success/15 text-success border border-success/30 hover:bg-success/25",
        link: "text-accent underline-offset-4 hover:underline",
      },
      size: {
        xs: "h-6 px-2 text-[11px] rounded-sm",
        sm: "h-7 px-2.5 text-xs",
        md: "h-8 px-3 text-[13px]",
        lg: "h-9 px-4 text-sm",
        icon: "h-8 w-8",
        "icon-sm": "h-7 w-7",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  kbd?: string;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, kbd, children, ...props }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props}>
    {children}
    {kbd ? <kbd className="key ml-1 opacity-70">{kbd}</kbd> : null}
  </button>
));
Button.displayName = "Button";

export { buttonVariants };
