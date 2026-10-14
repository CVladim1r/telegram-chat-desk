import type { Chat, Message } from "./types";


export function mergeMessages(
  current: Message[],
  incoming: Message[],
  source: "snapshot" | "event" = "snapshot",
): Message[] {
  const result = [...current];

  for (const message of incoming) {
    const matches = (old: Message) =>
      old.id === message.id ||
      Boolean(
        message.client_message_id &&
          old.client_message_id === message.client_message_id,
      );
    const index = result.findIndex(matches);

    if (index === -1) {
      result.push(message);
      continue;
    }

    const old = result[index];
    if (old.id > 0 && message.id < 0) continue;
    const merged =
      source === "snapshot" && old.id > 0 && message.id > 0
        ? { ...message, ...old }
        : { ...old, ...message };

    if (message.id > 0) delete merged.requestState;
    result[index] = merged;
  }
  return result.sort(
    (a, b) =>
      Date.parse(a.created_at) - Date.parse(b.created_at) || a.id - b.id,
  );
}

export function upsertChat(chats: Chat[], chat: Chat): Chat[] {
  const previous = chats.find((item) => item.id === chat.id);
  const next =
    previous && Date.parse(previous.updated_at) > Date.parse(chat.updated_at)
      ? previous
      : chat;
  return [...chats.filter((item) => item.id !== chat.id), next].sort(
    (a, b) =>
      Date.parse(b.updated_at) - Date.parse(a.updated_at) || b.id - a.id,
  );
}


export function validText(text: string): boolean {
  return Boolean(text.trim()) && Array.from(text).length <= 4096;
}

export function codepoints(text: string): number {
  return Array.from(text).length;
}
