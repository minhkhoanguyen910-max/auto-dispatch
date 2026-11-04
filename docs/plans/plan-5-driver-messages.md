# Plan 5: Driver messages in the bot

Status: **planned** (2026-10-30, revised the same day: replies instead of buttons, orders given instead of offered,
on duty by message), nothing built. Branch **`plan-5-driver-messages`** (on top of `plan-1-overload`), in its own
folder `../telegram-dispatch-system-plan5` with its own test server (`-p plan5`, web on 8191, the test bot); the
test-week server (`-p plan4`, 8190) isn't touched. Built in full, then tested by the user (see
[plan-4-meeting-points.md](plan-4-meeting-points.md) for the test copy and [plan-1-test-week.md](plan-1-test-week.md)),
one step at a time, each explained and agreed before it's built. Merged into `main` only when the user says so.

## For whoever builds this

- Read this whole file first; the **Decisions** are the user's and aren't re-argued.
- Live runs from `/home/oogway/telegram-dispatch-system` on `main` and must not change. Test in the test copy with a
  test driver connected to the dispatcher's own Telegram (@ForTestDispatchBot); never the live bot's token.
- Test with `ROUTE_OPTIMIZATION` off and on, and `MEETING_POINTS` parked and all.

## The idea

Today drivers talk to the dispatcher in Telegram the way they're used to: they **reply to an order's message** with
when they'll get there ("10"), "here" when they arrive and "done" when it's delivered, and they also send messages that
aren't about any order. They don't want to change that. So the bot **understands those same replies**, and the web gets
a **Chat** tab where the dispatcher reads every driver message and writes back. Buttons stay for drivers who like
them; nobody has to use them.

## Decisions (user, 2026-10-30)

1. **No new buttons for arrival times.** The driver replies to the order's message with a number of minutes, as they
   do now. The times are irregular and usually under 15 minutes.
2. **"here" = Arrived, "done" = Delivered**, as replies to the order's message. A few other common words count too
   (below); the list can grow.
3. **Replies to an order's message, and replies to those replies, belong to that order.** Other messages aren't about
   an order.
4. **A separate Chat tab** in the web: every driver message, about an order or not, and the dispatcher's answers.
5. **One order on the go:** a number, "here" or "done" that isn't a reply counts for that order; with several, the bot
   asks "Which order? Reply to its message."
6. **The driver's chat is tidied as today:** once an order is finished, its messages are deleted from the driver's
   Telegram, the driver's own replies included. Everything stays in the web's Chat tab and on the order.
7. **The words are edited in the web** (settings, next to "My reply"): one list for arrived, one for delivered. A word
   can't be in both lists, and numbers can't be words (they're minutes).
8. **Replies instead of buttons:** the card no longer has On my way / Arrived / Delivered / the minutes buttons; only
   ✏️ Report a change and 📌 I'm parked here stay. Replies come first; the Chat tab after.
9. **Orders are given, not offered:** no Accept / Decline and no 60-second offer. "Send to: a driver" gives the order
   straight to them; "Send to: Automatic" lets the app choose among the drivers **on duty** (rule B with route
   planning on) and gives it straight away. No setting to bring offers back. A driver who can't take one says so by
   message; the dispatcher uses Give to another driver.
10. **On duty by message:** the driver sends `start` (also `on`, `hi`) to begin and `finish` (also `off`, `bye`) to end
    (editable lists). Off duty: no new orders; orders they have stay theirs. The dispatcher sees who's on duty and can
    switch it. Nobody on duty: an Automatic order waits as "No driver".

## How the bot understands a message

**Which order:** the message the driver replied to (or, when it isn't a reply and the driver has exactly one order on
the go, that order; with several, the bot asks "Which order? Reply to its message."). The bot remembers which order every message in the driver's chat
belongs to: the order card, the bot's confirmations and notices, the dispatcher's messages about an order, and the
driver's own messages. So a reply to a reply (or to a confirmation) still finds the order. A message that isn't a reply,
or replies to something not about an order, isn't about an order.

**What it means** (the whole message, ignoring capitals, spaces and full stops):

| The driver writes | Meaning | What happens |
|---|---|---|
| `10`, `10 min`, `10mins`, `in 10`, `10m` (1–180) | arrives in 10 min | The dispatcher sees "Driver says ~14:35" (and, when it's 2–3 min, the usual "contact the customer now" alert). Can be sent again ("5" later). |
| `here`, `arrived`, `outside` (editable list) | Arrived | Status Arrived (from Accepted the app records On my way first) |
| `done`, `delivered`, `dn`, ✅, 👍 (editable list) | Delivered | Status Delivered (through Arrived if not tapped), as if the button was tapped |
| anything else (about an order) | a message about the order | Shown on the order and in the Chat tab, with a pop-up for the dispatcher |
| anything not about an order | a chat message | Chat tab only, with a pop-up |

- **The bot answers each understood reply** in one short line under it, so the driver knows it worked: "✅ #12:
  arrives ~14:35, the dispatcher was told" / "📍 #12: arrived" / "🎉 #12 delivered. Thank you!".
- **Can't apply** (the order was canceled, is already delivered, or isn't theirs): the bot says so, and the message
  still goes to the dispatcher.
- **A dialog in progress wins:** while the bot is asking for new quantities or a meeting spot, a typed message
  answers that, as today.
- With route planning on, "here" on a stop that isn't the next one is still recorded (the driver's reality wins) and
  the route is re-planned.

## The Chat tab (web)

- **Left:** drivers, newest message first, with the number of unread messages; a pop-up and beep for new ones (like
  change reports today).
- **Right:** one driver's conversation, like Telegram: their messages and the dispatcher's, with the time; messages
  about an order carry a tag "#12" that opens the order.
- **Writing back:** the dispatcher types and sends; the bot delivers it to the driver ("💬 Dispatcher: ..."). Replying
  to a message in the chat ties the answer to the same order, and in Telegram it appears as a reply to the driver's
  message, so the driver's next reply is tied to that order too.
- **On the order page:** the messages about that order, newest last.
- Read means a dispatcher opened that driver's conversation (shared by all dispatchers).
- Drivers never see customers' details through the app; what the dispatcher types is up to them.

## Photos, voice messages, locations

At first shown as "📷 Photo", "🎤 Voice message", "📍 Location", "📎 File", with an **Open** link (the web fetches the
file from Telegram when clicked). Photos as small pictures in the chat and playing voice messages in the web can come
later if wanted.

## Data

`driver_messages`: driver, order (none for chat messages), direction (from the driver / from the dispatcher / the
bot's confirmation), kind (text, photo, voice, location, file), text, Telegram message id, the message it replied to,
Telegram file id, the dispatcher who sent it, sent at, read at. The word lists are app settings (`app_settings`, like
the reply template), with the defaults above. The bot's existing tracked messages (`order_messages`)
and the order cards are looked up too, to find the order a reply belongs to.

## Steps

1. **Orders given, not offered; on duty.** "Send to" a driver or Automatic gives the order straight away (no Accept /
   Decline, no offer timeout); Automatic chooses among on-duty drivers. Drivers' on duty / off duty, shown and
   switchable on the Drivers page and in the order form.
2. **Replies instead of buttons.** The bot remembers which order every message in a driver's chat belongs to (cards,
   its own messages, the driver's replies). Replies: minutes → "Driver says ~14:35"; `here` → Arrived; `done` →
   Delivered; with the one-line confirmation; a number or word that isn't a reply counts when one order is on the go.
   `start` / `finish` (not replies) → on / off duty. Anything else about an order → to the dispatcher (as today's
   change report alert, until step 4). The card's status and minutes buttons go.
3. **Words editable in the web** (settings): arrived, delivered, start, finish.
4. **Messages and the Chat tab, reading:** `driver_messages` (every message, with its order or none); drivers list,
   unread counts, conversations, pop-ups; an order's messages on its page.
5. **Chat tab, writing back:** to a driver, optionally about an order (sent as a Telegram reply).
6. **Photos, voice messages, locations, files:** shown with an Open link.
7. **Tidying:** the driver's replies deleted from their Telegram with the order's other messages once it's finished.
8. **Tests:** automatic tests for each step (route planning off and on, meeting points parked and all), then the
   user's test with their own Telegram as the test driver on the Plan 5 server.

## Open questions

None. (Answered 2026-10-30: one order on the go counts; drivers' replies are deleted with the order's other messages;
the words are editable in the web.)
