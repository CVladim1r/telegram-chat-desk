export interface BotInfo {
  id: string;
  username: string;
  first_name: string;
}

export interface ConnectionState {
  status: "disconnected" | "connecting" | "connected" | "reconnecting" | "error";
  error_code: string | null;
  message: string | null;
}

export interface Session {
  bot: BotInfo;
  connection: ConnectionState;
}

export interface Contact {
  telegram_chat_id: string;
  name: string;
  username: string | null;
  available: boolean;
}


export interface Chat extends Contact {
  id: number;
  last_message: string | null;
  updated_at: string;
}

export interface Message {
  id: number;
  chat_id: number;
  telegram_chat_id: string;
  direction: "incoming" | "outgoing";
  text: string;
  source: "user" | "system";
  created_at: string;
  telegram_message_id: string | null;
  client_message_id: string | null;
  status: "queued" | "sending" | "sent" | "failed" | "unknown" | "received";
  error_code: string | null;
  error_message: string | null;
  requestState?: "pending" | "uncertain" | "rejected";

}

export interface MessagesPage {
  items: Message[];
  next_before_id: number | null;
}


export interface Bootstrap extends Session {
  chats: Chat[];
  cursor: number;
}

export type ChangeEvent =
  | { id: number; type: "chat.upsert"; data: Chat }
  | { id: number; type: "message.upsert"; data: Message }
  | { id: number; type: "contact.upsert"; data: Contact };



  export interface Changes {
  items: ChangeEvent[];
  cursor: number;
  has_more: boolean;
  connection: ConnectionState;
}

