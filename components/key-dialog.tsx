"use client";

import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useKey } from "@/lib/key";

export function KeyDialog() {
  const { key, setKey } = useKey();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [masked, setMasked] = useState(true);
  useEffect(() => setMasked(CSS.supports("-webkit-text-security", "disc")), []);

  function save() {
    const value = draft.trim();
    if (!value) return;
    setKey(value);
    setDraft("");
    setOpen(false);
  }

  function forget() {
    setKey(null);
    setDraft("");
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (!next) setDraft(""); }}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <KeyRound />
          {key ? "Change key" : "Use my key"}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Run live with your Mistral key</DialogTitle>
          <DialogDescription>
            Each live request sends the key to this console&apos;s API, which passes it to Mistral and nowhere else.
            It is kept in this tab&apos;s memory only. Reloading the page forgets it.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor="mistral-key">Mistral API key</Label>
          {/* Not a login form: no <form>, no name, and a text input masked by CSS, because password
              managers offer to save any type="password" field. Browsers without text-security
              fall back to type="password" so the key is never shown in clear. */}
          <Input
            id="mistral-key"
            type={masked ? "text" : "password"}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") save(); }}
            placeholder={key ? "A key is set. Paste a new one to replace it." : "Paste your key"}
            className="[-webkit-text-security:disc]"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            data-1p-ignore
            data-lpignore="true"
            data-bwignore
            data-form-type="other"
          />
        </div>
        <DialogFooter>
          {key && (
            <Button variant="ghost" onClick={forget} className="mr-auto">
              Forget key and return to Replay
            </Button>
          )}
          <Button onClick={save} disabled={!draft.trim()}>
            Use this key
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
