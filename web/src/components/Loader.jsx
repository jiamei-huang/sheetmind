import React from "react";

export default function Loader({ label = "Loading..." }) {
  return (
    <div className="flex items-center justify-center gap-3 text-sm text-slate-500">
      <span className="relative flex h-3 w-3">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-70" />
        <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500" />
      </span>
      <span>{label}</span>
    </div>
  );
}
