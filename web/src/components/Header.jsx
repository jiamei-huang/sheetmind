import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart3, Clock3, Menu, RotateCcw, UserRound } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  clearAnonymousBrowserState,
  getAnonymousSession,
  resetAnonymousSession,
} from "../api/session.js";

export default function Header({ onOpenNavigation }) {
  const navigate = useNavigate();
  const [retentionDays, setRetentionDays] = useState(null);
  const [isResetOpen, setIsResetOpen] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [resetError, setResetError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getAnonymousSession()
      .then((session) => {
        if (!cancelled) setRetentionDays(session.retentionDays);
      })
      .catch(() => {
        if (!cancelled) setRetentionDays(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleReset = async () => {
    setIsResetting(true);
    setResetError("");
    try {
      await resetAnonymousSession();
      clearAnonymousBrowserState();
      window.location.reload();
    } catch {
      setResetError("Could not reset the demo. Please try again.");
      setIsResetting(false);
    }
  };

  return (
    <header className="fixed left-0 top-0 z-40 flex h-[60px] w-full items-center border-b border-slate-200 bg-white">
      <div className="w-full px-4 sm:px-6 lg:px-8 flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            onClick={onOpenNavigation}
            aria-label="Open navigation"
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 lg:hidden"
          >
            <Menu className="h-5 w-5" />
          </button>
          <button
            onClick={() => navigate("/")}
            className="flex min-w-0 cursor-pointer items-center transition-opacity hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
          <div className="mr-2.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-blue-600">
            <BarChart3 className="h-5 w-5 text-white" />
          </div>
          <span className="text-lg font-semibold text-slate-950">SheetMind</span>
          </button>
        </div>
        {/* Anonymous demo controls */}
        <div className="flex items-center">
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button
                className="flex items-center focus:outline-none font-medium text-black/80 hover:text-black px-2 sm:px-3 py-2 rounded-md hover:bg-slate-50 transition-colors"
                aria-label="Anonymous Demo settings"
              >
                <UserRound className="w-5 h-5 sm:mr-2" />
                <span className="hidden sm:inline">Anonymous Demo</span>
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="end"
                sideOffset={4}
                className="min-w-[240px] rounded shadow-lg bg-white p-1 border border-slate-100 z-50"
              >
                <DropdownMenu.Label className="flex items-start gap-2 px-3 py-2 text-xs font-normal text-slate-500">
                  <Clock3 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  <span>
                    Data expires after {retentionDays ?? "the configured number of"} days of inactivity.
                  </span>
                </DropdownMenu.Label>
                <DropdownMenu.Separator className="h-px bg-slate-100 my-1" />
                <DropdownMenu.Item
                  onSelect={() => setIsResetOpen(true)}
                  className="flex items-center px-3 py-2 rounded cursor-pointer hover:bg-red-50 focus:bg-red-50 focus:outline-none text-red-600"
                >
                  <RotateCcw className="w-4 h-4 mr-2" />
                  Reset Demo Data
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </div>

      {isResetOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/40 px-4">
          <div className="sm-dialog w-full max-w-sm p-6" role="alertdialog" aria-modal="true" aria-labelledby="reset-demo-title">
            <h2 id="reset-demo-title" className="text-base font-semibold text-slate-900">Reset Demo Data</h2>
            <p className="mt-2 text-sm text-slate-600">
              This permanently deletes your uploaded workbooks, projects, tasks, and analysis history.
            </p>
            {resetError && <p className="mt-3 text-sm text-red-600">{resetError}</p>}
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setIsResetOpen(false);
                  setResetError("");
                }}
                disabled={isResetting}
                className="px-4 py-2 text-sm font-medium text-slate-700 border border-slate-200 rounded-md hover:bg-slate-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleReset}
                disabled={isResetting}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 disabled:bg-red-300"
              >
                {isResetting ? "Resetting..." : "Reset"}
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
