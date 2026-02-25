"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useAppStore } from "@/stores/appStore";

export function TickerSearch() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const setSelectedTicker = useAppStore((s) => s.setSelectedTicker);

  const filtered: string[] = [];

  const submit = useCallback(
    (ticker: string) => {
      const symbol = ticker.toUpperCase();
      setSelectedTicker(symbol);
      setOpen(false);
      setQuery("");
      setSelectedIndex(0);
    },
    [setSelectedTicker]
  );

  // Listen for keydown to open search when user starts typing
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Ignore if user is typing in an input/textarea already
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      // Ignore modifier keys
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      // Open on alphanumeric key press
      if (/^[a-zA-Z0-9]$/.test(e.key)) {
        e.preventDefault();
        setOpen(true);
        setQuery(e.key.toUpperCase());
        setSelectedIndex(0);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    }
  }, [open]);

  // Handle input keydown for navigation
  const onInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      setQuery("");
      setSelectedIndex(0);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filtered.length > 0) {
        submit(filtered[selectedIndex]);
      } else if (query.length > 0) {
        // Allow direct ticker entry even if not in watchlist
        submit(query);
      }
    }
  };

  if (!open) return null;

  return (
    <div className="absolute inset-0 z-50 flex items-start justify-center pt-16 bg-black/60">
      <div className="w-80 bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl overflow-hidden">
        <div className="p-3">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value.toUpperCase());
              setSelectedIndex(0);
            }}
            onKeyDown={onInputKeyDown}
            onBlur={() => {
              // Delay to allow click events on list items
              setTimeout(() => {
                setOpen(false);
                setQuery("");
                setSelectedIndex(0);
              }, 150);
            }}
            placeholder="Type ticker symbol..."
            className="w-full bg-zinc-800 border border-zinc-600 rounded px-3 py-2 text-white text-sm font-mono placeholder-zinc-500 outline-none focus:border-emerald-500"
          />
        </div>
        {filtered.length > 0 && (
          <div className="max-h-48 overflow-y-auto border-t border-zinc-800">
            {filtered.map((ticker, i) => (
              <button
                key={ticker}
                onMouseDown={(e) => {
                  e.preventDefault();
                  submit(ticker);
                }}
                className={`w-full text-left px-4 py-2 text-sm font-mono transition-colors ${
                  i === selectedIndex
                    ? "bg-emerald-600/30 text-emerald-400"
                    : "text-zinc-300 hover:bg-zinc-800"
                }`}
              >
                {ticker}
              </button>
            ))}
          </div>
        )}
        {query.length > 0 && filtered.length === 0 && (
          <div className="border-t border-zinc-800 px-4 py-3">
            <span className="text-zinc-500 text-xs">
              Press Enter to search <span className="text-emerald-400 font-mono">{query}</span>
            </span>
          </div>
        )}
        <div className="border-t border-zinc-800 px-4 py-2">
          <span className="text-zinc-600 text-xs">
            Type to search &middot; &uarr;&darr; navigate &middot; Enter to select &middot; Esc to close
          </span>
        </div>
      </div>
    </div>
  );
}
