import { useEffect, useRef, useState } from "react";

import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  ChevronDown,
  Clock3,
  Copy,
  ExternalLink,
  LoaderCircle,
  LogOut,
  MessageCircle,
  MoreHorizontal,
  Plus,
  Power,
  Search,
  Send,
  TriangleAlert,
  X,
} from "lucide-react";

import { api, ApiError, errorText, isAborted } from "./api";
import { codepoints, validText } from "./state";
import type { BotInfo, Contact, Message } from "./types";
import { useDesk } from "./useDesk";


const time = (value: string) =>
  new Date(value).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });

const dateLabel = (value: string) =>
  new Date(value).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

const initials = (name: string) =>
  name.split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase() || "?";

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <MessageCircle size={21} strokeWidth={2.4} />
      </span>
      <span>
        Диалоги<span className="brand-caption">TELEGRAM DESK</span>
      </span>
    </div>
  );
}

function Spinner() {
  return <LoaderCircle className="spin" size={18} aria-label="Загрузка" />;
}

function Alert({ children }: { children: ReactNode }) {
  return (
    <div className="alert" role="alert">
      <TriangleAlert size={17} />
      <span>{children}</span>
    </div>
  );
}


function Avatar({
  name,
  seed = 0,
  small = false,
}: {
  name: string;
  seed?: number;
  small?: boolean;
}) {
  return (
    <span
      aria-hidden="true"
      className={`avatar tone-${seed % 5} ${small ? "small" : ""}`}
    >
      {initials(name)}
    </span>
  );
}


function Login({
  connect,
  busy,
  notice,
}: {
  connect: (token: string) => Promise<void>;
  busy: boolean;
  notice: string;
}) {
  const [token, setToken] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!token.trim() || busy) return;
    const value = token;
    setToken("");
    await connect(value);
  }

  return (
    <main className="login-page">
      <div className="login-backdrop" aria-hidden="true">
        <span className="backdrop-circle circle-one" />
        <span className="backdrop-circle circle-two" />
        <span className="backdrop-circle circle-three" />
        <span className="backdrop-blob" />
        <span className="backdrop-line line-one" />
        <span className="backdrop-line line-two" />
        <span className="backdrop-line line-three" />
      </div>
      
      <div className="login-layout">
        <div className="login-card-scene">
          <div className="login-back-panel" aria-hidden="true">
            <span>Вход в диалоги</span>
          </div>
          
          <section className="login-card" aria-labelledby="login-title">
            <div className="login-card-body">
              <header className="login-card-heading">
                <h1 id="login-title">Подключить бота</h1>
                <p id="token-help">
                  Введите токен из BotFather, чтобы открыть ваши диалоги.
                </p>
              </header>
              
              <form onSubmit={submit}>
                <div className="login-field-heading">
                  <label htmlFor="bot-token">Токен бота</label>
                  <a
                    href="https://t.me/BotFather"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Где взять токен? <ExternalLink size={12} />
                  </a>
                </div>

                <input
                  id="bot-token"
                  type="password"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder="Вставьте токен"
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  disabled={busy}
                  required
                  aria-describedby="token-help"
                />
                {notice && <Alert>{notice}</Alert>}
                <button
                  className="primary connect-button"
                  disabled={busy || !token.trim()}
                >
                  {busy ? (
                    <>
                      <Spinner /> Подключаем…
                    </>
                  ) : (
                    <>
                      Подключить бота <ArrowRight size={17} />
                    </>
                  )}
                </button>
              </form>
              <p className="privacy-note">Токен не сохраняется в браузере.</p>
            </div>
          </section>
        </div>

        <aside className="login-note">
          <strong>Пригласите собеседника</strong>
          <p>
            Поделитесь ссылкой на бота. Когда собеседник нажмёт «Начать», его
            диалог появится здесь.
          </p>
        </aside>
      </div>
    </main>
  );
}

function BotLink({ bot }: { bot: BotInfo }) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const url = `https://t.me/${bot.username}`;
  useEffect(() => {
    if (!copied) return;
    const id = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(id);
  }, [copied]);
  return (
    <div className="bot-link-wrap">
      <div className="bot-link">
        <a href={url} target="_blank" rel="noreferrer">
          <Send size={17} />
          <span>@{bot.username}</span>
          <ExternalLink size={14} />
        </a>
        <button
          className="icon-button"
          title="Скопировать ссылку на бота"
          aria-label="Скопировать ссылку на бота"
          onClick={() =>
            void navigator.clipboard
              .writeText(url)
              .then(() => {
                setCopied(true);
                setCopyFailed(false);
              })
              .catch(() => setCopyFailed(true))
          }
        >
          {copied ? <Check size={17} /> : <Copy size={17} />}
        </button>
      </div>
      {copied && <small role="status">Ссылка скопирована</small>}
      {copyFailed && (
        <small role="status">Скопируйте адрес через меню ссылки.</small>
      )}
    </div>
  );
}

function NewChat({
  bot,
  close,
  create,
  onUnauthorized,
}: {
  bot: BotInfo;
  close: () => void;
  create: (id: string) => Promise<void>;
  onUnauthorized: (error: unknown) => void;
}) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [query, setQuery] = useState("");
  const [id, setId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const modal = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    modal.current?.querySelector<HTMLInputElement>("input")?.focus();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(
      () =>
        void api<{ items: Contact[] }>(
          `/contacts?q=${encodeURIComponent(query)}`,
          { signal: controller.signal },
        )
          .then((data) => {
            setContacts(data.items);
            setError("");
          })
          .catch((cause) => {
            if (isAborted(cause)) return;
            if (cause instanceof ApiError && cause.status === 401)
              onUnauthorized(cause);
            else setError(errorText(cause));
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          }),
      150,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, onUnauthorized]);
  async function open(contactId: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await create(contactId);
      close();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  }
  function keydown(event: KeyboardEvent) {
    if (event.key === "Escape" && !busy) close();
    if (event.key !== "Tab") return;
    const elements = modal.current?.querySelectorAll<HTMLElement>(
      "button:not(:disabled), input:not(:disabled), a[href]",
    );
    if (!elements?.length) return;
    const first = elements[0],
      last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    }
    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) close();
      }}
    >
      <div
        className="modal"
        ref={modal}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-chat-title"
        onKeyDown={keydown}
      >
        <div className="modal-title">
          <div>
            <span className="eyebrow">НОВЫЙ ДИАЛОГ</span>
            <h2 id="new-chat-title">С кем пообщаемся?</h2>
          </div>
          <button
            className="icon-button"
            onClick={close}
            disabled={busy}
            aria-label="Закрыть"
          >
            <X size={21} />
          </button>
        </div>
        <p className="muted">Здесь люди, которые уже писали вашему боту.</p>
        <div className="search-field">
          <Search size={18} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Поиск контакта"
            placeholder="Имя, username или ID"
          />
        </div>
        <div className="contact-list">
          {loading ? (
            <div className="center-note">
              <Spinner /> Загружаем контакты…
            </div>
          ) : contacts.length ? (
            contacts.map((contact) => (
              <button
                className="contact-row"
                key={contact.telegram_chat_id}
                disabled={busy || !contact.available}
                onClick={() => void open(contact.telegram_chat_id)}
              >
                <Avatar
                  name={contact.name}
                  seed={Number(contact.telegram_chat_id.slice(-2))}
                />
                <span>
                  <strong>{contact.name}</strong>
                  <small>
                    {contact.available
                      ? contact.username
                        ? `@${contact.username}`
                        : `ID ${contact.telegram_chat_id}`
                      : "Бот недоступен получателю"}
                  </small>
                </span>
                <ArrowRight size={17} />
              </button>
            ))
          ) : (
            <div className="center-note">
              {query ? "Контакт не найден" : "Пока нет подключённых контактов"}
            </div>
          )}
        </div>
        <form
          className="id-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (/^[1-9][0-9]{0,19}$/.test(id.trim())) void open(id.trim());
          }}
        >
          <label htmlFor="contact-id">Или введите известный ID чата</label>
          <div>
            <input
              id="contact-id"
              inputMode="numeric"
              value={id}
              onChange={(event) => setId(event.target.value)}
              placeholder="Например, 123456789"
            />
            <button
              className="primary"
              disabled={busy || !/^[1-9][0-9]{0,19}$/.test(id.trim())}
            >
              {busy ? <Spinner /> : <ArrowRight size={19} />}
              <span className="sr-only">Открыть чат по ID</span>
            </button>
          </div>
        </form>
        {error && <Alert>{error}</Alert>}
        <div className="invite-note">
          <p>
            Чтобы появился новый контакт, отправьте человеку ссылку на бота и
            попросите нажать <strong>«Начать»</strong>.
          </p>
          <BotLink bot={bot} />
        </div>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  retry,
}: {
  message: Message;
  retry: (message: Message) => Promise<void>;
}) {
  const outgoing = message.direction === "outgoing";
  const labels = {
    queued: "В очереди",
    sending: "Отправляется",
    sent: "Отправлено в Telegram",
    failed: "Не отправлено",
    unknown: "Результат неизвестен",
    received: "Получено",
  };
  const status =
    message.requestState === "uncertain"
      ? "Проверить принятие сервером"
      : message.requestState === "pending"
        ? "Отправляется на сервер"
        : labels[message.status];
  const issue =
    message.status === "failed" ||
    message.status === "unknown" ||
    message.requestState === "uncertain";
  return (
    <div className={`message-row ${outgoing ? "outgoing" : "incoming"}`}>
      <div className={`message-bubble ${issue ? "message-issue" : ""}`}>
        {message.source === "system" && (
          <span className="system-label">Ответ бота</span>
        )}
        <p>{message.text}</p>
        <div className="message-meta">
          <time dateTime={message.created_at}>{time(message.created_at)}</time>
          {outgoing && (
            <span title={status} aria-label={status}>
              {issue ? (
                <TriangleAlert size={13} />
              ) : message.status === "sent" ? (
                <Check size={15} />
              ) : (
                <Clock3 size={12} />
              )}
            </span>
          )}
        </div>
        {issue && (
          <div className="message-error">
            <strong>{status}</strong>
            <span>{message.error_message}</span>
            {message.status === "unknown" && (
              <span>
                Проверьте Telegram перед новой отправкой: сообщение могло уже
                прийти.
              </span>
            )}
            {message.requestState === "uncertain" && (
              <button onClick={() => void retry(message)}>
                Проверить отправку
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const desk = useDesk();
  const [search, setSearch] = useState("");
  const [newChat, setNewChat] = useState(false);
  const [menu, setMenu] = useState(false);
  const [disconnectConfirm, setDisconnectConfirm] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [atBottom, setAtBottom] = useState(true);
  const viewport = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const connectionPanel = useRef<HTMLDivElement>(null);
  const connectionToggle = useRef<HTMLButtonElement>(null);
  const previousSelected = useRef<number | null>(null);
  const nearBottom = useRef(true);
  const paginationAnchor = useRef<{
    id: number;
    height: number;
    top: number;
  } | null>(null);
  const active = desk.chats.find((chat) => chat.id === desk.selected);
  const items = active ? (desk.messages[active.id] ?? []) : [];
  const draft = active ? (drafts[active.id] ?? "") : "";
  const count = codepoints(draft);
  const visibleChats = desk.chats.filter((chat) =>
    `${chat.name} ${chat.username ?? ""} ${chat.telegram_chat_id}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );

  useEffect(() => {
    if (desk.phase !== "ready") {
      setDrafts({});
      setNewChat(false);
      setMenu(false);
      setSearch("");
      setDisconnectConfirm(false);
    }
  }, [desk.phase]);
  useEffect(() => {
    if (!menu) return;
    function outside(event: PointerEvent) {
      if (!connectionPanel.current?.contains(event.target as Node))
        setMenu(false);
    }
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setMenu(false);
        setDisconnectConfirm(false);
        connectionToggle.current?.focus();
      }
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [menu]);
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const anchor = paginationAnchor.current;
    if (anchor && anchor.id === desk.selected && !desk.loadingHistory) {
      element.scrollTop = anchor.top + element.scrollHeight - anchor.height;
      paginationAnchor.current = null;
    } else if (
      desk.selected !== previousSelected.current ||
      nearBottom.current
    ) {
      element.scrollTop = element.scrollHeight;
      nearBottom.current = true;
      setAtBottom(true);
    }
    previousSelected.current = desk.selected;
  }, [desk.selected, items, desk.loadingHistory]);
  useEffect(() => {
    if (input.current) {
      input.current.style.height = "auto";
      input.current.style.height = `${Math.min(input.current.scrollHeight, 150)}px`;
    }
  }, [draft]);

  if (desk.phase === "loading")
    return (
      <main className="initial-loading">
        <Brand />
        <Spinner />
        <p>Открываем рабочее пространство…</p>
      </main>
    );
  if (desk.phase === "login" || !desk.session)
    return (
      <Login connect={desk.connect} busy={desk.busy} notice={desk.notice} />
    );

  const { bot, connection } = desk.session;
  const connected = connection.status === "connected" && !desk.networkIssue;
  const connectionLabel = desk.networkIssue
    ? "Восстанавливаем связь с сервером"
    : {
        connected: "Бот подключён",
        connecting: "Подключение…",
        reconnecting: "Переподключение к Telegram",
        disconnected: "Бот отключён",
        error: "Ошибка подключения",
      }[connection.status];
  const statusTone = connected
    ? "connected"
    : connection.status === "error"
      ? "error"
      : "waiting";
  const statusLabel = connected
    ? "Подключён"
    : desk.networkIssue || connection.status === "reconnecting"
      ? "Восстанавливаем связь"
      : {
          connected: "Подключён",
          connecting: "Подключение",
          reconnecting: "Восстанавливаем связь",
          disconnected: "Отключён",
          error: "Ошибка подключения",
        }[connection.status];
  const canSend =
    active?.available &&
    connection.status !== "error" &&
    connection.status !== "disconnected";
  function send() {
    if (!active || !validText(draft) || !canSend) return;
    desk.send(active, draft);
    setDrafts((previous) => ({ ...previous, [active.id]: "" }));
    nearBottom.current = true;
    input.current?.focus();
  }
  function composerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing &&
      event.keyCode !== 229
    ) {
      event.preventDefault();
      send();
    }
  }

  return (
    <main className={`desk ${active ? "chat-open" : ""}`}>
      <aside className="sidebar" aria-label="Диалоги">
        <div className="sidebar-heading">
          <h1>
            Сообщения <span>{desk.chats.length}</span>
          </h1>
          <button
            className="icon-button add-chat"
            onClick={() => setNewChat(true)}
            title="Новый диалог"
            aria-label="Новый диалог"
          >
            <Plus size={21} />
          </button>
        </div>
        <div className="sidebar-search search-field">
          <Search size={17} />
          <input
            aria-label="Поиск диалога"
            placeholder="Найти диалог"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {search && (
            <button
              className="icon-button"
              onClick={() => setSearch("")}
              aria-label="Очистить поиск"
            >
              <X size={14} />
            </button>
          )}
        </div>
        <div className="list-label">ВСЕ ДИАЛОГИ</div>
        <nav className="chat-list" aria-label="Список диалогов">
          {visibleChats.length ? (
            visibleChats.map((chat) => (
              <button
                className={`chat-item ${chat.id === desk.selected ? "selected" : ""}`}
                key={chat.id}
                onClick={() => {
                  paginationAnchor.current = null;
                  desk.setSelected(chat.id);
                }}
                aria-current={chat.id === desk.selected ? "page" : undefined}
              >
                <Avatar name={chat.name} seed={chat.id} />
                <span className="chat-preview">
                  <span className="chat-title">
                    <strong>{chat.name}</strong>
                    <time dateTime={chat.updated_at}>
                      {time(chat.updated_at)}
                    </time>
                  </span>
                  <span className="preview-text">
                    {chat.available
                      ? (chat.last_message ?? "Начните диалог")
                      : "Получатель недоступен"}
                  </span>
                </span>
              </button>
            ))
          ) : (
            <div className="sidebar-empty">
              <MessageCircle size={28} />
              <strong>
                {search ? "Ничего не найдено" : "Здесь будут ваши диалоги"}
              </strong>
              <p>
                {search
                  ? "Попробуйте другое имя или ID."
                  : "Как только кто-то напишет боту, диалог появится в списке."}
              </p>
              {!search && (
                <button
                  className="text-button"
                  onClick={() => setNewChat(true)}
                >
                  Начать общение <ArrowRight size={14} />
                </button>
              )}
            </div>
          )}
        </nav>
        <div className="sidebar-footer" ref={connectionPanel}>
          <div className="bot-profile">
            <span className="bot-avatar">
              <Bot size={21} />
            </span>
            <div className="bot-identity">
              <strong title={bot.first_name}>{bot.first_name}</strong>
              <span
                className={`bot-status ${statusTone}`}
                role="status"
                title={connection.message || connectionLabel}
              >
                <span className="status-dot" />
                {statusLabel}
              </span>
            </div>
            <button
              ref={connectionToggle}
              className={`icon-button connection-toggle ${menu ? "active" : ""}`}
              aria-label="Настройки подключения"
              aria-expanded={menu}
              aria-controls="connection-menu"
              onClick={() => {
                setMenu(!menu);
                setDisconnectConfirm(false);
              }}
            >
              <MoreHorizontal size={19} />
            </button>
          </div>
          {menu && (
            <div
              id="connection-menu"
              className="session-menu"
              role="region"
              aria-label="Управление подключением"
            >
              {disconnectConfirm ? (
                <div className="disconnect-confirm">
                  <span className="disconnect-icon">
                    <Power size={18} />
                  </span>
                  <h3>Отключить бота?</h3>
                  <p>
                    Получение и отправка сообщений остановятся для всех вкладок.
                    История сохранится.
                  </p>
                  <div className="disconnect-actions">
                    <button
                      disabled={desk.busy}
                      onClick={() => setDisconnectConfirm(false)}
                    >
                      Отмена
                    </button>
                    <button
                      disabled={desk.busy}
                      className="disconnect-submit"
                      onClick={() => void desk.endSession(true)}
                    >
                      Отключить
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="connection-menu-heading">
                    <span>Подключение</span>
                    <strong>@{bot.username}</strong>
                  </div>
                  <button
                    className="connection-action"
                    disabled={desk.busy}
                    onClick={() => void desk.endSession()}
                  >
                    <span className="action-icon">
                      <LogOut size={17} />
                    </span>
                    <span>
                      <strong>Выйти из сеанса</strong>
                      <small>Бот продолжит работать</small>
                    </span>
                  </button>
                  <div className="menu-divider" />
                  <button
                    className="connection-action disconnect-action"
                    disabled={desk.busy}
                    onClick={() => setDisconnectConfirm(true)}
                  >
                    <span className="action-icon">
                      <Power size={17} />
                    </span>
                    <span>
                      <strong>Отключить бота</strong>
                      <small>Остановить обмен сообщениями</small>
                    </span>
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </aside>

      <section
        className="workspace"
        aria-label={active ? `Диалог с ${active.name}` : "Рабочее пространство"}
      >
        {desk.notice && (
          <div className="top-notice" role="alert">
            <TriangleAlert size={17} />
            <span>{desk.notice}</span>
            <button
              className="icon-button"
              onClick={() => desk.setNotice("")}
              aria-label="Закрыть уведомление"
            >
              <X size={16} />
            </button>
          </div>
        )}
        {!connected && (
          <div className="connection-banner" role="status">
            <span className="dot" />
            {connection.message || connectionLabel}
          </div>
        )}
        {active ? (
          <>
            <header className="conversation-header">
              <button
                className="icon-button mobile-back"
                onClick={() => desk.setSelected(null)}
                aria-label="Назад к диалогам"
              >
                <ArrowLeft size={21} />
              </button>
              <Avatar name={active.name} seed={active.id} small />
              <div className="conversation-identity">
                <h2>{active.name}</h2>
                <p>
                  {active.username
                    ? `@${active.username}`
                    : `ID ${active.telegram_chat_id}`}
                </p>
                {!active.available && (
                  <span className="recipient-status">
                    Получатель недоступен
                  </span>
                )}
              </div>
            </header>
            <div className="message-stage">
              <div
                className="messages"
                ref={viewport}
                onScroll={() => {
                  const element = viewport.current!;
                  nearBottom.current =
                    element.scrollHeight -
                      element.scrollTop -
                      element.clientHeight <
                    100;
                  setAtBottom(nearBottom.current);
                }}
              >
                <div className="message-content">
                  {desk.pageBefore[active.id] && (
                    <button
                      className="older-button"
                      disabled={desk.loadingHistory}
                      onClick={() => {
                        if (viewport.current)
                          paginationAnchor.current = {
                            id: active.id,
                            height: viewport.current.scrollHeight,
                            top: viewport.current.scrollTop,
                          };
                        void desk.loadOlder();
                      }}
                    >
                      {desk.loadingHistory ? (
                        <Spinner />
                      ) : (
                        <ChevronDown size={15} className="rotate" />
                      )}{" "}
                      Загрузить предыдущие
                    </button>
                  )}
                  {desk.loadingHistory && items.length === 0 && (
                    <div className="center-note">
                      <Spinner /> Загружаем историю…
                    </div>
                  )}
                  {desk.historyError && (
                    <div className="history-error">
                      <Alert>{desk.historyError}</Alert>
                      <button
                        className="text-button"
                        onClick={desk.reloadHistory}
                      >
                        Повторить
                      </button>
                    </div>
                  )}
                  {!desk.loadingHistory &&
                    !desk.historyError &&
                    items.length === 0 && (
                      <div className="conversation-empty">
                        <MessageCircle size={30} />
                        <h3>Начните с «Привет»</h3>
                        <p>
                          Сообщение придёт собеседнику от имени @{bot.username}.
                        </p>
                      </div>
                    )}
                  {items.map((message, index) => (
                    <div key={message.client_message_id || message.id}>
                      {(index === 0 ||
                        dateLabel(items[index - 1].created_at) !==
                          dateLabel(message.created_at)) && (
                        <div className="date-divider">
                          <span>{dateLabel(message.created_at)}</span>
                        </div>
                      )}
                      <MessageBubble
                        message={message}
                        retry={desk.retryRequest}
                      />
                    </div>
                  ))}
                </div>
              </div>
              {!atBottom && (
                <button
                  className="scroll-bottom icon-button"
                  aria-label="К последним сообщениям"
                  onClick={() => {
                    viewport.current?.scrollTo({
                      top: viewport.current.scrollHeight,
                      behavior: "smooth",
                    });
                  }}
                >
                  <ArrowDown size={20} />
                </button>
              )}
            </div>
            <div className="composer-area">
              {!active.available && (
                <p className="recipient-warning">
                  Получатель должен снова разрешить сообщения от бота в
                  Telegram.
                </p>
              )}
              <form
                className={`composer ${count > 4096 ? "invalid" : ""}`}
                onSubmit={(event) => {
                  event.preventDefault();
                  send();
                }}
              >
                <textarea
                  ref={input}
                  rows={1}
                  aria-label="Текст сообщения"
                  aria-invalid={count > 4096 || undefined}
                  aria-describedby={
                    count >= 3800 ? "message-length" : undefined
                  }
                  placeholder={
                    canSend
                      ? "Написать сообщение…"
                      : "Отправка сейчас недоступна"
                  }
                  value={draft}
                  disabled={!canSend}
                  onChange={(event) =>
                    setDrafts((previous) => ({
                      ...previous,
                      [active.id]: event.target.value,
                    }))
                  }
                  onKeyDown={composerKey}
                />
                {count >= 3800 && (
                  <span
                    id="message-length"
                    className={`composer-count ${count > 4096 ? "danger-text" : ""}`}
                    role="status"
                  >
                    {count.toLocaleString("ru-RU")} / 4 096
                  </span>
                )}
                <button
                  className="send-button"
                  type="submit"
                  disabled={!canSend || !validText(draft)}
                  aria-label="Отправить сообщение"
                >
                  <Send size={19} />
                </button>
              </form>
            </div>
          </>
        ) : (
          <div className="welcome">
            <span className="welcome-art">
              <span className="welcome-ring" />
              <MessageCircle size={47} strokeWidth={1.5} />
              <span className="welcome-plane">
                <Send size={20} />
              </span>
            </span>
            <span className="eyebrow">ПРОСТРАНСТВО ДЛЯ ОБЩЕНИЯ</span>
            <h2>
              {desk.chats.length
                ? "Продолжим разговор?"
                : "Первый диалог — впереди"}
            </h2>
            <p>
              {desk.chats.length
                ? "Выберите диалог слева или начните новый. Всё необходимое для общения уже здесь."
                : "Поделитесь ссылкой на бота. Когда собеседник нажмёт «Начать» и напишет вам, здесь появится его сообщение."}
            </p>
            <BotLink bot={bot} />
            <button className="primary" onClick={() => setNewChat(true)}>
              <Plus size={18} /> Новый диалог
            </button>
          </div>
        )}
      </section>
      {newChat && (
        <NewChat
          bot={bot}
          close={() => setNewChat(false)}
          create={desk.createChat}
          onUnauthorized={desk.handleError}
        />
      )}
    </main>
  );
}
