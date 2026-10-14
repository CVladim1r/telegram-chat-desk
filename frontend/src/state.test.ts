import { describe, expect, it } from "vitest";
import { codepoints, mergeMessages, upsertChat, validText } from "./state";

import type { Chat, Message } from "./types";


const base: Message = {
  id: 10,
  chat_id: 1,
  telegram_chat_id: "123",
  direction: "outgoing",
  text: "Привет",
  source: "user",
  created_at: "2026-10-14T12:00:00Z",
  telegram_message_id: null,
  client_message_id: "request-one",
  status: "queued",
  error_code: null,
  error_message: null,
};

describe("message reconciliation", () => {
  it("replaces optimistic message when changes arrive before the POST response without duplicating", () => {
    const optimistic = { ...base, id: -1, requestState: "pending" as const };
    const sent = {
      ...base,
      status: "sent" as const,
      telegram_message_id: "71",
    };
    const result = mergeMessages(mergeMessages([optimistic], [sent], "event"), [
      base,
    ]);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: 10,
      status: "sent",
      telegram_message_id: "71",
    });

    expect(result[0].requestState).toBeUndefined();
  });
  it("ignores late uncertain HTTP outcome after a successful event", () => {
    const sent = { ...base, status: "sent" as const };
    const uncertain = { ...base, id: -1, requestState: "uncertain" as const };
    expect(mergeMessages([sent], [uncertain])).toEqual([sent]);
  });
  it("accepts authoritative rate-limit requeue and subsequent progress", () => {
    const sending = { ...base, status: "sending" as const };
    let messages = mergeMessages(
      [sending],
      [{ ...base, error_code: "RATE_LIMITED" }],
      "event",
    );

    expect(messages[0].status).toBe("queued");
    messages = mergeMessages(messages, [sending]);
    expect(messages[0].status).toBe("queued");
    messages = mergeMessages(messages, [sending], "event");
    messages = mergeMessages(messages, [{ ...base, status: "sent" }], "event");
    messages = mergeMessages(messages, [base]);
    expect(messages[0].status).toBe("sent");
  });

  it("preserves unknown result against queued POST replay and never creates a new request ID", () => {
    const result = mergeMessages([{ ...base, status: "unknown" }], [base]);
    expect(result).toHaveLength(1);
    expect(result[0].status).toBe("unknown");
    expect(result[0].client_message_id).toBe("request-one");
  });

  it("deduplicates overlapping history pages, using date then id for stable order", () => {
    const older = {
      ...base,
      id: 5,
      client_message_id: null,
      created_at: "2026-10-13T12:00:00Z",
    };
    const next = { ...base, id: 11, client_message_id: "next" };
    expect(
      mergeMessages([next, base], [base, older]).map((message) => message.id),
    ).toEqual([5, 10, 11]);
  });

  it("does not collapse incoming messages with null client ids", () => {
    const incoming = {
      ...base,
      direction: "incoming" as const,
      status: "received" as const,
      client_message_id: null,
    };

    expect(mergeMessages([incoming], [{ ...incoming, id: 11 }])).toHaveLength(
      2,
    );
  });
});

describe("text validation", () => {
  it("counts astral emoji in code points, in agreement with Python len", () => {
    expect(codepoints("😀")).toBe(1);
    expect(validText("😀".repeat(4096))).toBe(true);
    expect(validText("😀".repeat(4097))).toBe(false);
  });
  it("rejects empty or whitespace-only text but retains user formatting", () => {
    expect(validText(" \n\t")).toBe(false);
    expect(validText(" <b>Привет</b>\n")).toBe(true);
  });
});



describe("chat ordering", () => {
  const chat: Chat = {
    id: 1,
    telegram_chat_id: "1",
    name: "Анна",
    username: null,
    available: true,
    last_message: "Новое",
    updated_at: "2026-10-14T12:00:00Z",
  };
  it("does not regress a chat preview when creation response arrives after changes", () => {
    expect(
      upsertChat([chat], {
        ...chat,
        last_message: "Старое",
        updated_at: "2026-10-13T12:00:00Z",
      }),
    ).toEqual([chat]);
  });
});

