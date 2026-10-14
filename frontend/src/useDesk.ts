import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorText, isAborted } from "./api";
import { mergeMessages, upsertChat, validText } from "./state";

import type {
  Bootstrap,
  Changes,
  Chat,
  Message,
  MessagesPage,
  Session,
} from "./types";


export function useDesk() {
  const [phase, setPhase] = useState<"loading" | "login" | "ready">("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [messages, setMessages] = useState<Record<number, Message[]>>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [networkIssue, setNetworkIssue] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState("");

  const [pageBefore, setPageBefore] = useState<Record<number, number | null>>(
    {},
  );

  const [historyRevision, setHistoryRevision] = useState(0);
  const epoch = useRef(0);
  const cursor = useRef(0);
  const localId = useRef(-1);
  const selectedRef = useRef(selected);

  selectedRef.current = selected;

  const requests = useRef(new Set<AbortController>());
  const pending = useRef(new Set<string>());


  const request = useCallback(
    async <T>(path: string, init: RequestInit = {}): Promise<T> => {
      const controller = new AbortController();
      requests.current.add(controller);
      try {
        return await api<T>(path, {
          ...init,
          signal: init.signal ?? controller.signal,
        });
      } finally {
        requests.current.delete(controller);
      }
    },
    [],
  );

  const clear = useCallback((message = "") => {
    epoch.current += 1;
    for (const controller of requests.current) controller.abort();
    requests.current.clear();
    pending.current.clear();
    setSession(null);
    setChats([]);
    setMessages({});
    setSelected(null);
    setPageBefore({});
    setPhase("login");
    setNotice(message);
    setBusy(false);
    setNetworkIssue(false);
    setHistoryError("");
  }, []);


  const handleError = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401)
        clear("Сессия завершена. Подключите бота снова.");
      else if (!isAborted(error)) setNotice(errorText(error));
    },
    [clear],
  );

  const applyBootstrap = useCallback((data: Bootstrap) => {
    cursor.current = data.cursor;
    setSession({ bot: data.bot, connection: data.connection });
    setChats(
      [...data.chats].sort(
        (a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at),
      ),
    );
    setMessages({});
    setPageBefore({});
    setSelected(null);
    setPhase("ready");
    setNotice("");
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const generation = epoch.current;
    void (async () => {
      try {
        await api<Session>("/session", { signal: controller.signal });
        const data = await api<Bootstrap>("/bootstrap", {
          signal: controller.signal,
        });
        if (!controller.signal.aborted && generation === epoch.current)
          applyBootstrap(data);
      } catch (error) {
        if (controller.signal.aborted || generation !== epoch.current) return;
        setPhase("login");
        if (!(error instanceof ApiError && error.status === 401))
          setNotice(errorText(error));
      }
    })();

    return () => {
      controller.abort();
      for (const item of requests.current) item.abort();
    };
  }, [applyBootstrap]);


  async function connect(token: string) {
    const generation = epoch.current;
    setBusy(true);
    setNotice("");

    try {
      await request<Session>("/session", {
        method: "POST",
        body: JSON.stringify({ bot_token: token.trim() }),
      });

      const data = await request<Bootstrap>("/bootstrap");
      if (generation === epoch.current) applyBootstrap(data);
    } catch (error) {
      if (generation === epoch.current && !isAborted(error))
        setNotice(errorText(error));
    } finally {
      if (generation === epoch.current) setBusy(false);
    }
  }

  useEffect(() => {
    if (phase !== "ready" || !session) return;
    const generation = epoch.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;

    async function poll() {
      let delay = 1000;
      try {
        const data = await api<Changes>(
          `/changes?cursor=${cursor.current}&limit=100`,
          { signal: controller.signal },
        );

        if (controller.signal.aborted || generation !== epoch.current) return;
        for (const event of data.items) {
          if (event.type === "chat.upsert")
            setChats((previous) => upsertChat(previous, event.data));

          if (event.type === "message.upsert") {
            const message = event.data;
            setMessages((previous) => ({
              ...previous,
              [message.chat_id]: mergeMessages(
                previous[message.chat_id] ?? [],
                [message],
                "event",
              ),
            }));
          }
        }
        cursor.current = data.cursor;
        setSession((previous) =>
          previous ? { ...previous, connection: data.connection } : null,
        );
        setNetworkIssue(false);
        failures = 0;
        delay = data.has_more ? 0 : 1000;
      } catch (error) {
        if (controller.signal.aborted || generation !== epoch.current) return;

        if (error instanceof ApiError && error.status === 401) {
          handleError(error);
          return;
        }

        setNetworkIssue(true);
        failures += 1;
        delay = Math.min(30_000, 1000 * 2 ** Math.min(failures, 5));

        if (error instanceof ApiError && error.retryAfter) delay = Math.max(delay, error.retryAfter * 1000);
      }
      if (!controller.signal.aborted && generation === epoch.current)
        timer = setTimeout(poll, delay);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [phase, session?.bot.id, handleError]);

  useEffect(() => {
    if (selected === null || phase !== "ready") return;
    const id = selected;
    const generation = epoch.current;
    const controller = new AbortController();

    setLoadingHistory(true);
    setHistoryError("");

    void api<MessagesPage>(`/chats/${id}/messages?limit=50`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (controller.signal.aborted || generation !== epoch.current) return;
        setMessages((previous) => ({
          ...previous,
          [id]: mergeMessages(previous[id] ?? [], data.items),
        }));
        setPageBefore((previous) => ({
          ...previous,
          [id]: data.next_before_id,
        }));
      })
      .catch((error) => {
        if (controller.signal.aborted || generation !== epoch.current) return;

        if (error instanceof ApiError && error.status === 401) handleError(error);

        else setHistoryError(errorText(error));
      })
      .finally(() => {
        if (!controller.signal.aborted && generation === epoch.current)
          setLoadingHistory(false);
      });
    return () => controller.abort();
  }, [selected, phase, historyRevision, handleError]);


  async function loadOlder() {
    const id = selected;
    if (id === null || !pageBefore[id] || loadingHistory) return;
    const generation = epoch.current;
    setLoadingHistory(true);
    setHistoryError("");
    try {
      const data = await request<MessagesPage>(
        `/chats/${id}/messages?before_id=${pageBefore[id]}&limit=50`,
      );
      if (generation !== epoch.current) return;
      setMessages((previous) => ({
        ...previous,
        [id]: mergeMessages(previous[id] ?? [], data.items),
      }));
      setPageBefore((previous) => ({ ...previous, [id]: data.next_before_id }));
    } catch (error) {
      if (generation === epoch.current) handleError(error);
    } finally {
      if (generation === epoch.current && selectedRef.current === id)
        setLoadingHistory(false);
    }
  }

  async function submitMessage(message: Message) {
    const key = message.client_message_id!;
    if (pending.current.has(key)) return;
    pending.current.add(key);
    const generation = epoch.current;
    const put = (next: Message) =>
      setMessages((previous) => ({
        ...previous,
        [next.chat_id]: mergeMessages(previous[next.chat_id] ?? [], [next]),
      }));
    put({ ...message, requestState: "pending" });
    try {
      const result = await request<Message>(
        `/chats/${message.chat_id}/messages`,
        {
          method: "POST",
          body: JSON.stringify({ text: message.text, client_message_id: key }),
        },
      );
      if (generation === epoch.current) put(result);
    } catch (error) {
      if (generation !== epoch.current || isAborted(error)) return;

      if (error instanceof ApiError && error.status === 401) {
        handleError(error);
        return;
      }

      const rejected =
        error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 429;
      put({
        ...message,
        requestState: rejected ? "rejected" : "uncertain",
        status: rejected ? "failed" : "queued",
        error_message: errorText(error),
      });
    } finally {
      pending.current.delete(key);
    }
  }


  function send(chat: Chat, text: string) {
    if (!validText(text) || !chat.available) return;
    void submitMessage({
      id: localId.current--,
      chat_id: chat.id,
      telegram_chat_id: chat.telegram_chat_id,
      direction: "outgoing",
      text,
      source: "user",
      created_at: new Date().toISOString(),
      telegram_message_id: null,
      client_message_id: crypto.randomUUID(),
      status: "queued",
      error_code: null,
      error_message: null,
      requestState: "pending",
    });
  }

  async function createChat(telegram_chat_id: string): Promise<void> {
    const generation = epoch.current;
    try {
      const chat = await request<Chat>("/chats", {
        method: "POST",
        body: JSON.stringify({ telegram_chat_id }),
      });

      if (generation !== epoch.current) return;
      setChats((previous) => upsertChat(previous, chat));
      setSelected(chat.id);

    } catch (error) {
      if (
        generation === epoch.current &&
        error instanceof ApiError &&
        error.status === 401
      )
        handleError(error);
      throw error;
    }
  }

  async function endSession(disconnect = false) {
    const generation = epoch.current;
    setBusy(true);
    try {
      await request<void>(disconnect ? "/bot/disconnect" : "/session", {
        method: disconnect ? "POST" : "DELETE",
      });

      if (generation === epoch.current) clear();
    } catch (error) {
      if (generation === epoch.current) handleError(error);
    } finally {
      if (generation === epoch.current) setBusy(false);
    }
  }


  return {
    phase,
    session,
    chats,
    messages,
    selected,
    setSelected,
    notice,
    setNotice,
    networkIssue,
    busy,
    loadingHistory,
    historyError,
    pageBefore,
    connect,
    send,
    retryRequest: submitMessage,
    createChat,
    endSession,
    loadOlder,
    reloadHistory: () => setHistoryRevision((value) => value + 1),
    handleError,
  };
}
