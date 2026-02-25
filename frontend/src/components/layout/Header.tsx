"use client";

import { Activity } from "lucide-react";
import { useAppStore } from "@/stores/appStore";

export function Header() {
  const engineRunning = useAppStore((s) => s.engineRunning);
  const loadRefinedRsiPreset = useAppStore((s) => s.loadRefinedRsiPreset);

  return (
    <header className="h-14 bg-zinc-900 border-b border-zinc-800 flex items-center justify-between px-4">
      <div className="flex items-center gap-3">
        <button
          onClick={loadRefinedRsiPreset}
          className="flex items-center gap-2 hover:opacity-80 transition-opacity"
        >
          <Activity className="text-emerald-400" size={22} />
          <span className="text-lg font-bold text-white tracking-tight">SOREN</span>
        </button>
      </div>
      <div className="flex items-center gap-2">
        <div
          className={`w-2 h-2 rounded-full ${engineRunning ? "bg-emerald-400 animate-[pulse_0.8s_ease-in-out_infinite]" : "bg-red-500"}`}
        />
        <span className="text-sm text-zinc-400 uppercase tracking-wider font-medium">
          {engineRunning ? "LIVE TRADING" : "TRADING STOPPED"}
        </span>
      </div>
    </header>
  );
}
