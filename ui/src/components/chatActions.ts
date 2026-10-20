import { createContext, useContext } from "react";
import type { ToolEvent, Upload } from "../api";

/** Actions that cards inside the conversation (image results, approvals, old reverse-prompt cards) can trigger. */
export type ChatActions = {
  /** Attach a generated image to the composer, to build on it in the next message. */
  useAsBase: (file: string) => void;
  /** Put an image's prompt and settings into the composer as a request to generate it again. */
  reuse: (tool: ToolEvent) => void;
  /** Fill the composer with a message (and attachments), ready to send. */
  compose: (text: string, attachments?: Upload[]) => void;
  /** Answer an approval card of the running task. */
  approve: (toolId: string, decision: "allow" | "always" | "deny") => void;
};

export const ChatActionsContext = createContext<ChatActions | null>(null);

export function useChatActions(): ChatActions {
  const actions = useContext(ChatActionsContext);
  if (!actions) throw new Error("useChatActions outside ChatActionsContext");
  return actions;
}
