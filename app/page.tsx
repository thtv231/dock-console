import { redirect } from "next/navigation";

// The Overview is built in lesson 6; until then the console opens on the Inbox.
export default function Home() {
  redirect("/inbox");
}
