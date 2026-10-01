import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors duration-150 focus-ring disabled:pointer-events-none disabled:opacity-50 select-none",
  {
    variants: {
      variant: {
        primary: "bg-fg text-bg hover:opacity-90",
        accent: "bg-accent text-accent-fg hover:opacity-90",
        secondary: "border border-border bg-surface text-fg hover:bg-surface-2",
        ghost: "text-muted hover:bg-surface-2 hover:text-fg",
        destructive: "text-danger hover:bg-danger/10",
        "destructive-fill": "bg-danger text-white hover:opacity-90",
        link: "text-accent underline-offset-4 hover:underline",
        // kept for older call sites; render as neutral controls
        default: "bg-fg text-bg hover:opacity-90",
        outline: "border border-border bg-surface text-fg hover:bg-surface-2",
        danger: "text-danger hover:bg-danger/10",
        success: "border border-border bg-surface text-success hover:bg-success/10",
      },
      size: {
        xs: "h-6 px-2 text-[12px] rounded-sm",
        sm: "h-7 px-2.5 text-[12px]",
        md: "h-8 px-3 text-[13px]",
        lg: "h-9 px-4 text-[13px]",
        icon: "h-8 w-8",
        "icon-sm": "h-7 w-7",
        "icon-xs": "h-6 w-6 rounded-sm",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  kbd?: string;
}

const INVERTED = new Set(["primary", "default", "accent", "destructive-fill"]);

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, kbd, children, type = "button", ...props }, ref) => (
  <button ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} {...props}>
    {children}
    {kbd ? <kbd className={cn("key ml-0.5", INVERTED.has(variant || "") ? "border-current/20 bg-current/10 text-current opacity-90" : "opacity-80")}>{kbd}</kbd> : null}
  </button>
));
Button.displayName = "Button";

export { buttonVariants };
