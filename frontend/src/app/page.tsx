"use client";

import { Header } from "@/components/layout/Header";
import { StrategySidebar } from "@/components/layout/StrategySidebar";
import { DashboardGrid } from "@/components/layout/DashboardGrid";

export default function Home() {
  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Header />
      <div className="flex-1 flex overflow-hidden">
        <StrategySidebar />
        <DashboardGrid />
      </div>
    </div>
  );
}
