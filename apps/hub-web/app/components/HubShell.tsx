"use client";

import {
  type MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { createHttpHubClient } from "../../lib/hub/http-client";
import { createHttpOnboardingClient } from "../../lib/onboarding/http-client";
import {
  HubApiError,
  isHubConnectionFailure,
} from "../../lib/hub/client";
import {
  hubViewHref,
  parseHubView,
  type HubView,
} from "../../lib/hub/navigation";
import MemoriesView from "../features/memories/MemoriesView";
import GuidedDemoView from "../features/demo/GuidedDemoView";
import OnboardingView from "../features/onboarding/OnboardingView";
import RecallView from "../features/recall/RecallView";
import SystemView from "../features/system/SystemView";

type ConnectionState =
  | "checking"
  | "connected"
  | "unavailable"
  | "incompatible"
  | "error";

const connectionLabels: Record<ConnectionState, string> = {
  checking: "连接中",
  connected: "本地已连接",
  unavailable: "未连接",
  incompatible: "版本不兼容",
  error: "状态异常",
};

export default function HubShell({ initialView }: { initialView: HubView }) {
  const client = useMemo(() => createHttpHubClient(), []);
  const onboardingClient = useMemo(() => createHttpOnboardingClient(), []);
  const [view, setView] = useState<HubView>(initialView);
  const [namespace, setNamespace] = useState("正在读取本地命名空间");
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("checking");

  const observe = useCallback((repoKey: string) => {
    setNamespace(repoKey);
    setConnectionState("connected");
  }, []);
  const unavailable = useCallback(
    () => setConnectionState("unavailable"),
    [],
  );

  useEffect(() => {
    function restoreView() {
      setView(parseHubView(new URL(window.location.href).searchParams.get("view")));
    }
    window.addEventListener("popstate", restoreView);
    return () => window.removeEventListener("popstate", restoreView);
  }, []);

  useEffect(() => {
    if (view !== "recall" || connectionState !== "checking") return;
    const controller = new AbortController();
    client
      .system(controller.signal)
      .then((result) => observe(result.repo_key))
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        if (isHubConnectionFailure(reason)) {
          unavailable();
        } else {
          setConnectionState(
            reason instanceof HubApiError && reason.code === "invalid_response"
              ? "incompatible"
              : "error",
          );
        }
      });
    return () => controller.abort();
  }, [client, connectionState, observe, unavailable, view]);

  function navigate(
    event: MouseEvent<HTMLAnchorElement>,
    destination: HubView,
  ) {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    if (destination === view) return;
    window.history.pushState(null, "", hubViewHref(destination));
    setView(destination);
    window.scrollTo({ left: 0, top: 0 });
  }

  return (
    <main className="hub-root">
      <aside className="hub-sidebar">
        <div className="brand">
          <span className="cairn-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>
            <strong>CodeCairn</strong>
            <small>记忆中心</small>
          </span>
        </div>
        <nav aria-label="主导航">
          {(
            [
              ["memories", "记忆"],
              ["onboarding", "接入"],
              ["recall", "召回"],
              ["system", "系统"],
            ] as const
          ).map(([key, label]) => (
            <a
              key={key}
              className={view === key ? "active" : ""}
              aria-current={view === key ? "page" : undefined}
              href={hubViewHref(key)}
              onClick={(event) => navigate(event, key)}
            >
              {label}
            </a>
          ))}
        </nav>
      </aside>

      <section className="hub-stage">
        <header className="system-bar">
          <div>
            <small>{view === "demo" ? "当前页面" : "当前记忆命名空间"}</small>
            <strong>{view === "demo" ? "隔离示例空间" : namespace}</strong>
          </div>
          {view === "demo" ? (
            <span className="connection">示例演示</span>
          ) : (
            <span
              className={
                connectionState === "connected"
                  ? "connection connection-ok"
                  : connectionState === "checking"
                    ? "connection"
                    : "connection connection-failed"
              }
            >
              {connectionLabels[connectionState]}
            </span>
          )}
        </header>
        <div className="workspace">
          {view === "memories" ? (
            <MemoriesView
              client={client}
              onConnected={observe}
              onUnavailable={unavailable}
            />
          ) : view === "onboarding" ? (
            <OnboardingView
              client={onboardingClient}
              onConnected={observe}
              onUnavailable={unavailable}
            />
          ) : view === "recall" ? (
            <RecallView
              client={client}
              onConnected={observe}
              onUnavailable={unavailable}
            />
          ) : view === "system" ? (
            <SystemView
              client={client}
              onConnected={observe}
              onUnavailable={unavailable}
            />
          ) : (
            <GuidedDemoView />
          )}
        </div>
      </section>
    </main>
  );
}
