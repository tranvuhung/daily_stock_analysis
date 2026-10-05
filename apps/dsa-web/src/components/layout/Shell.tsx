import type React from 'react';
import { useEffect, useState } from 'react';
import { Menu } from 'lucide-react';
import { Outlet } from 'react-router-dom';
import { Drawer } from '../common/Drawer';
import { SidebarNav } from './SidebarNav';
import { DesktopUpdateIndicator } from './DesktopUpdateIndicator';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { AlertToasts } from '../alerts/AlertToasts';
import { useAlertStream } from '../../hooks/useAlertStream';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { t } = useUiLanguage();
  // Một kết nối SSE cảnh báo cho toàn app: toast, badge, giá live. One alert SSE stream for the app.
  useAlertStream();

  useEffect(() => {
    if (!mobileOpen) {
      return undefined;
    }

    const handleResize = () => {
      if (window.innerWidth >= 1024) {
        setMobileOpen(false);
      }
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [mobileOpen]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Thanh menu ngang ở đầu trang; mobile dùng nút mở Drawer. Top menu bar; mobile opens the Drawer. */}
      <header className="sticky top-0 z-40 border-b border-[var(--shell-sidebar-border)] bg-card/72 shadow-soft-card backdrop-blur-sm">
        <div className="mx-auto flex h-14 w-full max-w-[1680px] items-center gap-3 px-3 sm:px-4 lg:px-5">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border/70 bg-card/85 text-secondary-text transition-colors hover:bg-hover hover:text-foreground lg:hidden"
            aria-label={t('layout.openNav')}
          >
            <Menu className="h-5 w-5" />
          </button>
          <SidebarNav variant="top" actions={<DesktopUpdateIndicator />} />
        </div>
      </header>

      <div className="mx-auto w-full max-w-[1680px] px-3 py-3 sm:px-4 sm:py-4 lg:px-5">
        <main className="min-h-0 min-w-0 touch-pan-y">
          {children ?? <Outlet />}
        </main>
      </div>

      <Drawer
        isOpen={mobileOpen}
        onClose={() => setMobileOpen(false)}
        title={t('layout.navMenu')}
        width="max-w-xs"
        zIndex={90}
        side="left"
      >
        <SidebarNav onNavigate={() => setMobileOpen(false)} />
      </Drawer>
      <AlertToasts />
    </div>
  );
};
