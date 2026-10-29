"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useKey } from "@/lib/key";
import { cn } from "@/lib/utils";

export function ModeBadge() {
  const { key } = useKey();
  const live = key !== null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className={cn(
            "inline-flex h-6 items-center gap-1.5 rounded-sm border px-2 text-xs font-medium",
            live ? "border-live/50 bg-live/10 text-live" : "border-border bg-muted text-muted-foreground",
          )}
        >
          <span className={cn("size-1.5 rounded-full", live ? "bg-live" : "bg-muted-foreground/60")} />
          {live ? "Live" : "Replay"}
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-64">
        {live
          ? "Requests run on Mistral now, billed to your key."
          : "Results come from recorded real runs. No key needed."}
      </TooltipContent>
    </Tooltip>
  );
}
