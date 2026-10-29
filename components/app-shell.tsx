"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu, Moon, Sun, X } from "lucide-react";
import { KeyDialog } from "@/components/key-dialog";
import { ModeBadge } from "@/components/mode-badge";
import { Button } from "@/components/ui/button";
import { SCREENS, screenFor } from "@/lib/screens";
import { cn } from "@/lib/utils";

function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);

  function toggle() {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {}
    setDark(next);
  }

  return (
    <Button variant="ghost" size="sm" onClick={toggle} className="w-full justify-start text-muted-foreground">
      {dark ? <Sun /> : <Moon />}
      {dark ? "Light theme" : "Dark theme"}
    </Button>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const current = screenFor(pathname);
  return (
    <nav aria-label="Screens" className="flex flex-col gap-px px-2">
      {SCREENS.map((s) => {
        const Icon = s.icon;
        const row = "flex h-8 items-center gap-2.5 rounded-sm px-2 text-[13px]";
        if (!s.built) {
          return (
            <span key={s.href} aria-disabled="true" className={cn(row, "cursor-not-allowed text-muted-foreground/70")}>
              <Icon className="size-4 shrink-0" />
              <span className="flex-1 truncate">{s.title}</span>
              <span className="text-[11px] tabular-nums">Lesson {s.lesson}</span>
            </span>
          );
        }
        const active = current?.href === s.href;
        return (
          <Link
            key={s.href}
            href={s.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              row,
              "outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
              active
                ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground shadow-[inset_2px_0_0_var(--sidebar-primary)]"
                : "text-sidebar-foreground hover:bg-sidebar-accent/60",
            )}
          >
            <Icon className="size-4 shrink-0" />
            <span className="flex-1 truncate">{s.title}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <div className="px-4 py-3.5">
      <div className="text-sm font-semibold leading-tight">Dock Console</div>
      <div className="text-xs text-muted-foreground">Harbor Freight Co. operations</div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const screen = screenFor(pathname);
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex">
        <Brand />
        <Nav />
        <div className="mt-auto border-t border-sidebar-border p-2">
          <ThemeToggle />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-12 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur">
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            aria-label={navOpen ? "Close menu" : "Open menu"}
            aria-expanded={navOpen}
            onClick={() => setNavOpen(!navOpen)}
          >
            {navOpen ? <X /> : <Menu />}
          </Button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold">{screen?.title ?? "Dock Console"}</h1>
          </div>
          <ModeBadge />
          <KeyDialog />
        </header>

        {navOpen && (
          <div className="border-b bg-sidebar py-2 md:hidden">
            <Nav onNavigate={() => setNavOpen(false)} />
            <div className="mt-2 border-t border-sidebar-border px-2 pt-2">
              <ThemeToggle />
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
