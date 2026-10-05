import React, { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  Bell,
  BriefcaseBusiness,
  Gauge,
  Home,
  LogOut,
  MessageSquareQuote,
  Search,
  Settings2,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import {
  SCREENING_CONFIG_CHANGED_EVENT,
  SYSTEM_CONFIG_CHANGED_EVENT,
  screeningApi,
} from "../../api/screening";
import { useAuth } from "../../contexts/AuthContext";
import { useAgentChatStore } from "../../stores/agentChatStore";
import { useAlertStore } from "../../stores/alertStore";
import { useUiLanguage } from "../../contexts/UiLanguageContext";
import { formatUiText, type UiTextKey } from "../../i18n/uiText";
import { ALERT_REALTIME_TEXT } from "../../locales/featureText";
import { cn } from "../../utils/cn";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { StatusDot } from "../common/StatusDot";
import { UiLanguageToggle } from "../i18n/UiLanguageToggle";
import { ThemeToggle } from "../theme/ThemeToggle";

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  // "top": thanh menu ngang ở đầu trang (desktop). "top": horizontal top menu bar (desktop).
  variant?: "default" | "rail" | "top";
  // Nút bổ sung bên phải thanh top (vd: cập nhật desktop). Extra right-side actions for the top bar.
  actions?: React.ReactNode;
};

type NavItem = {
  key: string;
  labelKey: UiTextKey;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: "completion" | "alerts";
};

const NAV_ITEMS: NavItem[] = [
  {
    key: "home",
    labelKey: "layout.nav.home",
    to: "/",
    icon: Home,
    exact: true,
  },
  {
    key: "chat",
    labelKey: "layout.nav.chat",
    to: "/chat",
    icon: MessageSquareQuote,
    badge: "completion",
  },
  {
    key: "screening",
    labelKey: "layout.nav.screening",
    to: "/screening",
    icon: Search,
  },
  {
    key: "portfolio",
    labelKey: "layout.nav.portfolio",
    to: "/portfolio",
    icon: BriefcaseBusiness,
  },
  {
    key: "decision-signals",
    labelKey: "layout.nav.decisionSignals",
    to: "/decision-signals",
    icon: Activity,
  },
  {
    key: "backtest",
    labelKey: "layout.nav.backtest",
    to: "/backtest",
    icon: BarChart3,
  },
  {
    key: "alerts",
    labelKey: "layout.nav.alerts",
    to: "/alerts",
    icon: Bell,
    badge: "alerts",
  },
  { key: "usage", labelKey: "layout.nav.usage", to: "/usage", icon: Gauge },
  {
    key: "settings",
    labelKey: "layout.nav.settings",
    to: "/settings",
    icon: Settings2,
  },
];

// Tạm ẩn các menu chưa dùng; xoá key khỏi danh sách để hiện lại (route vẫn giữ nguyên).
// Temporarily hidden nav items; remove a key to show it again (routes are kept).
const HIDDEN_NAV_KEYS = new Set([
  "portfolio",
  "decision-signals",
  "backtest",
  "home",
  "chat",
  "usage",
]);

export const SidebarNav: React.FC<SidebarNavProps> = ({
  collapsed = false,
  onNavigate,
  variant = "default",
  actions,
}) => {
  const { authEnabled, logout } = useAuth();
  const { t, language } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const alertUnread = useAlertStore((state) => state.unreadCount);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [showScreeningNav, setShowScreeningNav] = useState(false);

  useEffect(() => {
    let active = true;

    const refreshScreeningStatus = async () => {
      try {
        const status = await screeningApi.getStatus();
        if (active) {
          setShowScreeningNav(status.enabled);
        }
      } catch {
        if (active) {
          setShowScreeningNav(false);
        }
      }
    };

    void refreshScreeningStatus();
    window.addEventListener(
      SCREENING_CONFIG_CHANGED_EVENT,
      refreshScreeningStatus,
    );
    window.addEventListener(
      SYSTEM_CONFIG_CHANGED_EVENT,
      refreshScreeningStatus,
    );

    return () => {
      active = false;
      window.removeEventListener(
        SCREENING_CONFIG_CHANGED_EVENT,
        refreshScreeningStatus,
      );
      window.removeEventListener(
        SYSTEM_CONFIG_CHANGED_EVENT,
        refreshScreeningStatus,
      );
    };
  }, []);

  const navItems = NAV_ITEMS.filter(
    (item) =>
      !HIDDEN_NAV_KEYS.has(item.key) &&
      (showScreeningNav || item.key !== "screening"),
  );
  const isRail = variant === "rail";
  const isTop = variant === "top";
  const itemBaseClass = isTop
    ? "group relative inline-flex h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-xl border border-transparent px-3 text-sm leading-none text-secondary-text transition-all"
    : cn(
        "group relative flex h-[var(--nav-item-height)] w-full items-center overflow-hidden rounded-2xl border border-transparent text-sm leading-none text-secondary-text transition-all",
        isRail
          ? "justify-center gap-2.5 px-2"
          : collapsed
            ? "justify-center px-0"
            : "gap-3 px-[var(--nav-item-padding-x)]",
      );
  const itemInteractiveClass = cn(
    itemBaseClass,
    "hover:bg-[var(--nav-hover-bg)] hover:text-foreground",
  );
  const itemActiveClass =
    "border-[var(--nav-active-border)] bg-[var(--nav-active-bg)] font-medium text-[hsl(var(--primary))]";
  const itemIconClass = cn(
    isRail || isTop ? "h-[18px] w-[18px]" : "h-5 w-5",
    "shrink-0",
  );
  const itemLabelClass = cn("truncate", isRail ? "text-center" : "");

  const navLinks = navItems.map(
    ({ key, labelKey, to, icon: Icon, exact, badge }) => {
      const label = t(labelKey);
      return (
        <NavLink
          key={key}
          to={to}
          end={exact}
          onClick={onNavigate}
          aria-label={label}
          className={({ isActive }) =>
            cn(itemInteractiveClass, isActive ? itemActiveClass : "")
          }
        >
          {({ isActive }) => (
            <>
              <Icon
                className={cn(
                  itemIconClass,
                  isActive
                    ? "text-[var(--nav-icon-active)]"
                    : "text-current",
                )}
              />
              {!collapsed ? (
                <span className={itemLabelClass}>{label}</span>
              ) : null}
              {badge === "completion" && completionBadge ? (
                <StatusDot
                  tone="info"
                  data-testid="chat-completion-badge"
                  className={cn(
                    "absolute border-2 border-background shadow-[0_0_10px_var(--nav-indicator-shadow)]",
                    isTop
                      ? "right-1 top-1"
                      : collapsed
                        ? "right-2 top-2"
                        : "right-3",
                  )}
                  aria-label={t("layout.newChatMessage")}
                />
              ) : null}
              {badge === "alerts" && alertUnread > 0 ? (
                <span
                  data-testid="alerts-unread-badge"
                  aria-label={formatUiText(
                    ALERT_REALTIME_TEXT[language].unreadBadge,
                    { count: alertUnread },
                  )}
                  className={cn(
                    "min-w-[1.25rem] rounded-full bg-[hsl(var(--color-danger-alert-text))] px-1.5 text-center text-[10px] font-semibold leading-5 text-white",
                    isTop ? "" : "absolute right-2",
                    collapsed ? "top-1" : "",
                  )}
                >
                  {alertUnread > 99 ? "99+" : alertUnread}
                </span>
              ) : null}
            </>
          )}
        </NavLink>
      );
    },
  );

  const logoutDialog = (
    <ConfirmDialog
      isOpen={showLogoutConfirm}
      title={t("layout.logoutTitle")}
      message={t("layout.logoutMessage")}
      confirmText={t("layout.logoutConfirm")}
      cancelText={t("common.cancel")}
      isDanger
      onConfirm={() => {
        setShowLogoutConfirm(false);
        onNavigate?.();
        void logout();
      }}
      onCancel={() => setShowLogoutConfirm(false)}
    />
  );

  if (isTop) {
    // Thanh menu ngang: logo + menu (desktop) + nút giao diện/ngôn ngữ/đăng xuất.
    // Horizontal bar: brand + nav links (desktop) + theme/language/logout actions.
    return (
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className="flex shrink-0 items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-[1rem] bg-primary-gradient text-[hsl(var(--primary-foreground))] shadow-[0_12px_28px_var(--nav-brand-shadow)]">
            <BarChart3 className="h-[19px] w-[19px]" />
          </div>
          <p className="text-[0.95rem] font-semibold leading-none text-foreground">
            DSA
          </p>
        </div>

        <nav
          className="hidden min-w-0 flex-1 items-center gap-1 overflow-x-auto lg:flex"
          aria-label={t("layout.mainNav")}
        >
          {navLinks}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-2">
          {actions}
          <UiLanguageToggle />
          <ThemeToggle />
          {authEnabled ? (
            <button
              type="button"
              onClick={() => setShowLogoutConfirm(true)}
              className="hidden h-9 cursor-pointer items-center gap-2 rounded-xl border border-transparent px-3 text-sm text-secondary-text transition-colors hover:bg-[var(--nav-hover-bg)] hover:text-foreground lg:inline-flex"
            >
              <LogOut className="h-[18px] w-[18px] shrink-0" />
              <span>{t("layout.logout")}</span>
            </button>
          ) : null}
        </div>

        {logoutDialog}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div
        className={cn(
          "flex items-center",
          isRail ? "mb-5 justify-center gap-2 pt-1" : "mb-4 gap-2 px-1",
          collapsed || isRail ? "justify-center" : "",
        )}
      >
        <div
          className={cn(
            "flex items-center justify-center bg-primary-gradient text-[hsl(var(--primary-foreground))] shadow-[0_12px_28px_var(--nav-brand-shadow)]",
            isRail ? "h-9 w-9 rounded-[1rem]" : "h-10 w-10 rounded-2xl",
          )}
        >
          <BarChart3 className={cn(isRail ? "h-[19px] w-[19px]" : "h-5 w-5")} />
        </div>
        {!collapsed ? (
          <p
            className={cn(
              "min-w-0 truncate font-semibold text-foreground",
              isRail ? "text-[0.95rem] leading-none" : "text-sm",
            )}
          >
            DSA
          </p>
        ) : null}
      </div>

      <nav
        className={cn("flex flex-col gap-1.5", isRail ? "" : "flex-1")}
        aria-label={t("layout.mainNav")}
      >
        {navLinks}

        <ThemeToggle
          variant={isRail ? "rail" : "nav"}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />
        <UiLanguageToggle
          variant={isRail ? "rail" : "nav"}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />
      </nav>

      {authEnabled ? (
        <button
          type="button"
          onClick={() => setShowLogoutConfirm(true)}
          className={cn(itemInteractiveClass, isRail ? "mt-1.5" : "mt-5")}
        >
          <LogOut className={itemIconClass} />
          {!collapsed ? (
            <span className={itemLabelClass}>{t("layout.logout")}</span>
          ) : null}
        </button>
      ) : null}

      {logoutDialog}
    </div>
  );
};
