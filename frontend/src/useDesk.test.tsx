import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDesk } from "./useDesk";

import type { Chat, Message } from "./types";


const bot = { id: "42", username: "example_bot", first_name: "Example" };
const connection = { status: "connected", error_code: null, message: null };
const chat: Chat = {
  id: 1,
  telegram_chat_id: "100",
  name: "Анна",
  username: null,
  available: true,
  last_message: null,
  updated_at: "2026-10-14T12:00:00Z",
};


const second = { ...chat, id: 2, telegram_chat_id: "200", name: "Иван" };
const message: Message = {
  id: 10,
  chat_id: 1,
  telegram_chat_id: "100",
  direction: "outgoing",
  text: "Привет",
  source: "user",
  created_at: "2026-10-14T12:00:00Z",
  telegram_message_id: null,
  client_message_id: null,
  status: "queued",
  error_code: null,
  error_message: null,
};


const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const ticks = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};


function setup(
  handler?: (
    path: string,
    init: RequestInit,
  ) => Promise<Response> | Response | undefined,
) {
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input);
      const custom = handler?.(path, init);
      if (custom) return custom;
      if (path === "/api/session") return response({ bot, connection });
      if (path === "/api/bootstrap")
        return response({ bot, connection, chats: [chat, second], cursor: 7 });
      if (path.startsWith("/api/changes"))
        return response({ items: [], cursor: 7, has_more: false, connection });
      if (path.includes("/messages"))
        return response({ items: [], next_before_id: null });
      throw new Error(`Unexpected request ${path}`);
    },
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}


beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});


describe("API lifecycle", () => {
  it("starts at snapshot cursor and drains paginated changes before the next poll", async () => {
    const fetcher = setup((path) => {
      if (path.includes("/changes?cursor=7"))
        return response({
          items: [{ id: 8, type: "message.upsert", data: message }],
          cursor: 8,
          has_more: true,
          connection,
        });
      if (path.includes("/changes?cursor=8"))
        return response({ items: [], cursor: 8, has_more: false, connection });
    });

    const { result } = renderHook(useDesk);
    await ticks();
    await ticks(1);
    expect(result.current.phase).toBe("ready");
    const paths = fetcher.mock.calls.map((call) => String(call[0]));
    expect(paths).toContain("/api/changes?cursor=7&limit=100");
    expect(paths).toContain("/api/changes?cursor=8&limit=100");
    expect(result.current.messages[1]).toHaveLength(1);
  });


  it("discards a late history response after selection moves to another chat", async () => {
    let resolveFirst!: (value: Response) => void;
    setup((path) => {
      if (path === "/api/chats/1/messages?limit=50")
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      if (path === "/api/chats/2/messages?limit=50")
        return response({
          items: [{ ...message, id: 20, chat_id: 2 }],
          next_before_id: null,
        });
    });

    const { result } = renderHook(useDesk);
    await ticks();
    act(() => result.current.setSelected(1));
    await ticks();
    act(() => result.current.setSelected(2));
    await ticks();
    await act(async () => {
      resolveFirst(response({ items: [message], next_before_id: null }));
    });
    expect(result.current.selected).toBe(2);
    expect(result.current.messages[2][0].id).toBe(20);
    expect(result.current.messages[1]).toBeUndefined();
    expect(result.current.loadingHistory).toBe(false);
  });


  it("retries an uncertain HTTP request with exactly the same client ID and text", async () => {
    const bodies: { client_message_id: string; text: string }[] = [];
    setup((path, init) => {
      if (path === "/api/chats/1/messages" && init.method === "POST") {
        const body = JSON.parse(String(init.body));
        bodies.push(body);
        if (bodies.length === 1)
          return Promise.reject(new TypeError("Network unavailable"));
        return response(
          {
            ...message,
            client_message_id: body.client_message_id,
            text: body.text,
          },
          202,
        );
      }
    });

    const { result } = renderHook(useDesk);
    await ticks();
    act(() => result.current.send(chat, "  Привет\n😀  "));
    await ticks();
    expect(result.current.messages[1][0].requestState).toBe("uncertain");

    await act(async () => {
      await result.current.retryRequest(result.current.messages[1][0]);
    });


    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0].text).toBe("  Привет\n😀  ");
    expect(result.current.messages[1]).toHaveLength(1);
    expect(result.current.messages[1][0].id).toBe(10);
    expect(result.current.messages[1][0].requestState).toBeUndefined();
  });


  it("clears private data and stops polling when the session expires", async () => {
    let polls = 0;
    const fetcher = setup((path) => {
      if (path.startsWith("/api/changes")) {
        polls += 1;
        return polls === 1
          ? response({
              items: [{ id: 8, type: "message.upsert", data: message }],
              cursor: 8,
              has_more: false,
              connection,
            })
          : response(
              {
                error: {
                  code: "SESSION_REQUIRED",
                  message: "Сессия завершена",
                },
              },
              401,
            );
      }
    });


    const { result } = renderHook(useDesk);

    await ticks();
    expect(result.current.messages[1]).toHaveLength(1);

    await ticks(1000);
    expect(result.current.phase).toBe("login");
    expect(result.current.messages).toEqual({});
    expect(result.current.chats).toEqual([]);
    expect(result.current.session).toBeNull();
    const count = fetcher.mock.calls.length;

    await ticks(30_000);

    expect(fetcher.mock.calls).toHaveLength(count);
  });
});
