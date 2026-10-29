"use client";

import { createContext, useContext, useState } from "react";

// The visitor's Mistral key lives in React state only: no localStorage, no cookie, no URL.
// Reloading the tab forgets it.
type KeyState = { key: string | null; setKey: (key: string | null) => void };

const KeyContext = createContext<KeyState>({ key: null, setKey: () => {} });

export function KeyProvider({ children }: { children: React.ReactNode }) {
  const [key, setKey] = useState<string | null>(null);
  return <KeyContext.Provider value={{ key, setKey }}>{children}</KeyContext.Provider>;
}

export function useKey() {
  return useContext(KeyContext);
}
