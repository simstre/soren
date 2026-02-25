"use client";

import { useState, useEffect } from "react";
import { ScanLine, Loader2, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAppStore } from "@/stores/appStore";
import { formatCompactNumber } from "@/lib/formatters";
import type { ScannerPreset, ScannerResult } from "@/lib/types";
import clsx from "clsx";

interface ScannerPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ScannerPanel({ isOpen, onClose }: ScannerPanelProps) {
  const [presets, setPresets] = useState<ScannerPreset[]>([]);
  const [results, setResults] = useState<ScannerResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const setSelectedTicker = useAppStore((s) => s.setSelectedTicker);

  useEffect(() => {
    let cancelled = false;
    api.getScannerPresets().then((data) => {
      if (!cancelled) setPresets(data);
    });
    return () => { cancelled = true; };
  }, []);

  const handleScan = async (presetId: string) => {
    setSelectedPreset(presetId);
    setLoading(true);
    try {
      const data = await api.runScanner(presetId);
      setResults(data);
    } catch {
      setResults([]);
    }
    setLoading(false);
  };

  const handleSelectSymbol = (symbol: string) => {
    setSelectedTicker(symbol);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl w-[600px] max-h-[80vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <ScanLine className="text-emerald-400" size={20} />
            <span className="text-white font-semibold">Stock Scanner</span>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white text-sm">
            Close
          </button>
        </div>

        {/* Presets */}
        <div className="px-5 py-3 border-b border-zinc-800">
          <div className="flex flex-wrap gap-2">
            {presets.map((preset) => (
              <button
                key={preset.id}
                onClick={() => handleScan(preset.id)}
                className={clsx(
                  "px-3 py-1.5 rounded-lg text-sm transition-colors",
                  selectedPreset === preset.id
                    ? "bg-emerald-600 text-white"
                    : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
                )}
              >
                {preset.name}
              </button>
            ))}
          </div>
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="animate-spin text-emerald-400" size={24} />
              <span className="ml-2 text-zinc-400">Scanning...</span>
            </div>
          ) : results.length === 0 ? (
            <div className="py-12 text-center text-zinc-500">
              Select a scanner preset to find stocks
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-zinc-900">
                <tr className="text-xs text-zinc-500 border-b border-zinc-800">
                  <th className="text-left px-5 py-2">Symbol</th>
                  <th className="text-right px-3 py-2">Price</th>
                  <th className="text-right px-3 py-2">Change</th>
                  <th className="text-right px-5 py-2">Volume</th>
                  <th className="text-center px-3 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/50">
                {results.map((r) => (
                  <tr key={r.symbol} className="hover:bg-zinc-800/30">
                    <td className="px-5 py-2.5">
                      <span className="text-white font-mono font-medium">{r.symbol}</span>
                      <span className="text-zinc-500 text-xs ml-2">{r.name}</span>
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-white">
                      ${r.price.toFixed(2)}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <span
                        className={clsx(
                          "flex items-center justify-end gap-0.5 font-mono",
                          r.change_pct >= 0 ? "text-emerald-400" : "text-red-400"
                        )}
                      >
                        {r.change_pct >= 0 ? (
                          <ArrowUpRight size={14} />
                        ) : (
                          <ArrowDownRight size={14} />
                        )}
                        {Math.abs(r.change_pct).toFixed(2)}%
                      </span>
                    </td>
                    <td className="px-5 py-2.5 text-right font-mono text-zinc-400">
                      {formatCompactNumber(r.volume)}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <button
                        onClick={() => handleSelectSymbol(r.symbol)}
                        className="text-xs text-emerald-400 hover:text-emerald-300 font-medium"
                      >
                        Trade
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
