/**
 * Draws technical analysis overlays (S/R lines, channels, pattern markers)
 * onto a Lightweight Charts instance.
 */
import {
  createSeriesMarkers,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type SeriesMarker,
  type Time,
  type SeriesType,
} from "lightweight-charts";
import type { AnalysisData, ChannelData } from "@/lib/types";

/** Handles currently applied to the chart so we can remove them later. */
export interface OverlayHandles {
  priceLines: IPriceLine[];
  markers: { setMarkers: (m: SeriesMarker<Time>[]) => void } | null;
  channelSeries: ISeriesApi<SeriesType>[];
}

const PATTERN_LABELS: Record<string, string> = {
  double_top: "Dbl Top",
  double_bottom: "Dbl Btm",
  head_and_shoulders: "H&S",
  inverse_head_and_shoulders: "Inv H&S",
  ascending_triangle: "Asc Tri",
  descending_triangle: "Dsc Tri",
};

const CHANNEL_COLORS: Record<string, string> = {
  ascending: "rgba(16, 185, 129, 0.45)",   // green
  descending: "rgba(239, 68, 68, 0.45)",    // red
  horizontal: "rgba(96, 165, 250, 0.45)",   // blue
};

export function clearOverlays(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  handles: OverlayHandles,
) {
  for (const pl of handles.priceLines) {
    series.removePriceLine(pl);
  }
  handles.priceLines = [];
  handles.markers?.setMarkers([]);

  for (const cs of handles.channelSeries) {
    chart.removeSeries(cs);
  }
  handles.channelSeries = [];
}

function drawChannel(
  chart: IChartApi,
  channel: ChannelData,
): ISeriesApi<SeriesType>[] {
  if (!channel.start_time || !channel.end_time) return [];
  if (channel.start_idx >= channel.end_idx) return [];

  const color = CHANNEL_COLORS[channel.direction] || CHANNEL_COLORS.horizontal;
  const result: ISeriesApi<SeriesType>[] = [];

  const startIdx = channel.start_idx;
  const endIdx = channel.end_idx;
  const startT = (new Date(channel.start_time).getTime() / 1000) as unknown as Time;
  const endT = (new Date(channel.end_time).getTime() / 1000) as unknown as Time;

  // Compute prices at start and end using the original index-based equation
  const upperStart = channel.upper_slope * startIdx + channel.upper_intercept;
  const upperEnd = channel.upper_slope * endIdx + channel.upper_intercept;
  const lowerStart = channel.lower_slope * startIdx + channel.lower_intercept;
  const lowerEnd = channel.lower_slope * endIdx + channel.lower_intercept;

  // Upper channel line
  const upperSeries = chart.addSeries(LineSeries, {
    color,
    lineWidth: 2,
    lineStyle: 2, // Dashed
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
  });
  upperSeries.setData([
    { time: startT, value: upperStart },
    { time: endT, value: upperEnd },
  ]);
  result.push(upperSeries);

  // Lower channel line
  const lowerSeries = chart.addSeries(LineSeries, {
    color,
    lineWidth: 2,
    lineStyle: 2,
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
  });
  lowerSeries.setData([
    { time: startT, value: lowerStart },
    { time: endT, value: lowerEnd },
  ]);
  result.push(lowerSeries);

  return result;
}

export function applyOverlays(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  analysis: AnalysisData,
): OverlayHandles {
  const priceLines: IPriceLine[] = [];
  const channelSeries: ISeriesApi<SeriesType>[] = [];

  // --- Support & Resistance lines ---
  const topLevels = [...analysis.sr_levels]
    .sort((a, b) => b.strength - a.strength)
    .slice(0, 8);

  for (const level of topLevels) {
    const isSupport = level.type === "support";
    const pl = series.createPriceLine({
      price: level.price,
      color: isSupport ? "rgba(16, 185, 129, 0.5)" : "rgba(239, 68, 68, 0.5)",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: `${isSupport ? "S" : "R"} (${level.touches})`,
    });
    priceLines.push(pl);
  }

  // --- Channels ---
  for (const channel of analysis.channels) {
    const lines = drawChannel(chart, channel);
    channelSeries.push(...lines);
  }

  return { priceLines, markers: null, channelSeries };
}
