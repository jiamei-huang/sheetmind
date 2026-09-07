import React from "react";

export default function Footer() {
  return (
    <footer className="fixed bottom-0 left-0 w-full bg-gray-100 py-3 border-t border-gray-200 text-gray-500 text-sm z-30">
      <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex justify-between items-center">
        <div>© 2025 SheetMind</div>
        <div className="hidden sm:block text-center">Privacy Policy <span className="mx-1">|</span> Terms of Service</div>
        <div className="text-right">Made with <span className="mx-0.5">❤️</span> using React, Tailwind CSS & Python (pandas + LangChain)</div>
      </div>
    </footer>
  );
}
