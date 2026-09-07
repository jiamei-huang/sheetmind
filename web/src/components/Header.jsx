import React from "react";
import { useNavigate } from "react-router-dom";
import { BarChart3, User, Settings, Clock, HelpCircle } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";

export default function Header() {
  const navigate = useNavigate();

  return (
    <header className="fixed top-0 left-0 w-full h-[60px] bg-white shadow z-40 flex items-center">
      <div className="w-full px-4 sm:px-6 lg:px-8 flex items-center justify-between">
        {/* 左侧图标与文字 - 可点击返回主页 */}
        <button
          onClick={() => navigate("/")}
          className="flex items-center h-full hover:opacity-80 transition-opacity cursor-pointer"
        >
          <div className="bg-blue-500 rounded-full w-10 h-10 flex items-center justify-center mr-3 flex-shrink-0">
            <BarChart3 className="text-white w-6 h-6" />
          </div>
          <div className="flex flex-col items-start">
            <span className="font-bold text-xl text-black leading-tight">SheetMind</span>
            <span className="text-xs text-gray-500 leading-tight -mt-0.5 text-left">
              An intelligent analysis assistant that lets you interact with Excel using natural language
            </span>
          </div>
        </button>
        {/* 右侧菜单与账号下拉 */}
        <div className="flex items-center">
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="flex items-center focus:outline-none font-medium text-black/80 hover:text-black px-3 py-2 rounded-md hover:bg-gray-50 transition-colors">
                <User className="w-5 h-5 mr-2" />
                <span>Account</span>
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content className="min-w-[180px] rounded shadow-lg bg-white p-1 border border-gray-100 mt-1">
                <DropdownMenu.Item className="flex items-center px-3 py-2 rounded cursor-pointer hover:bg-blue-50 text-black/90">
                  <Settings className="w-4 h-4 mr-2 opacity-70" />
                  Account Settings
                </DropdownMenu.Item>
                <DropdownMenu.Item className="flex items-center px-3 py-2 rounded cursor-pointer hover:bg-blue-50 text-black/90">
                  <Clock className="w-4 h-4 mr-2 opacity-70" />
                  Analysis History
                </DropdownMenu.Item>
                <DropdownMenu.Item className="flex items-center px-3 py-2 rounded cursor-pointer hover:bg-blue-50 text-black/90">
                  <HelpCircle className="w-4 h-4 mr-2 opacity-70" />
                  Help & Feedback
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </div>
    </header>
  );
}
