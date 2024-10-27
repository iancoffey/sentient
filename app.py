import os
import time
import tempfile
import logging
import threading
from queue import LifoQueue

from slack_bolt import App
from openai import OpenAI

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Slack app and OpenAI client
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Initialize OpenAI thread and assistant
secure_thread = client.beta.threads.create()
assistant_id = os.environ.get("ASSISTANT_ID")
assistant = client.beta.assistants.retrieve(assistant_id)

# Initialize message queue
stack_queue = LifoQueue()

def create_vector_store():
    """Creates a vector store using file resources for an assistant."""

    vector_store = client.beta.vector_stores.create(name="security_data")
    new_data = " ".join(stack_queue.queue).encode('utf-8')

    if not new_data:
        return

    with tempfile.NamedTemporaryFile(delete=True, suffix=".txt") as tmp:
        tmp.write(new_data)
        tmp.flush()

        file_resource = client.files.create(
            file=open(tmp.name, "rb"),
            purpose="assistants"
        )

        client.beta.vector_stores.files.create(
            vector_store_id=vector_store.id,
            file_id=file_resource.id
        )

    client.beta.assistants.update(
        assistant_id=assistant.id,
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
    )

def process_messages():
    """Periodically processes messages in the queue to update the vector store."""

    current_queue_depth = 0
    max_queue_depth = 500

    while True:
        time.sleep(10)

        logger.info(f"Queue size: {stack_queue.qsize()} "
                    f"Current depth: {current_queue_depth}")

        if stack_queue.qsize() == 0 or stack_queue.qsize() == current_queue_depth:
            continue

        if stack_queue.qsize() >= max_queue_depth:
            logger.warning("Max queue depth reached - not processing new documents")
            continue

        create_vector_store()
        current_queue_depth = stack_queue.qsize()

def query_thread(message, user, channel):
    """
    Processes questions from operators on the security situation,
    returns machine wisdom.

    Args:
        message: The message containing the operator's request.
        user: The ID of the user who sent the message.
        channel: The ID of the channel where the message was sent.
    """
    client.beta.threads.messages.create(
        thread_id=secure_thread.id,
        role="user",
        content=message
    )

    assistant_messages = []
    with client.beta.threads.runs.stream(
        thread_id=secure_thread.id,
        assistant_id=assistant.id
    ) as stream:
        for event in stream:
            if event.event == "thread.message.completed":
                assistant_messages.append(event.data.content[0].text.value)

    respond_to_user("\n".join(assistant_messages), user, channel)

def respond_to_user(message, user, channel):
    """
    Responds back to a specific user and channel with some wisdom.

    Args:
        message: The text of the message to send.
        user: The ID of the user to mention.
        channel: The ID of the channel to send the message to.
    """
    final_message = f"<@{user}>: {message}"

    app.client.chat_postMessage(
        channel=channel,
        text=final_message
    )

@app.event("app_mention")
def handle_app_mention_events(body, logger):
    """
    Handles the 'app_mention' event.
    Accepts questions from operators on the security situation,
    returns machine wisdom.

    Args:
        body: The request body containing event information.
        logger: A logger to log information.
    """
    try:
        event = body["event"]
        message = event["text"]
        user = event["user"]
        channel_id = event["channel"]

        mention_text = f"<@{app.client.auth_test()['user_id']}>"
        message = message.replace(mention_text, "").strip()

        query_thread(message, user, channel_id)
    except Exception as e:
        logger.error(f"Error handling app_mention event: {e}")

@app.event("reaction_added")
def handle_reaction_added(body, logger):
    """
    Reacting to messages with :eyes: in channels where the
    sentient bot exists.

    Args:
        body: The request body containing event information.
        logger: A logger to log information.
    """
    try:
        event = body["event"]
        reaction = event["reaction"]
        if reaction == "eyes":
            channel_id = event["item"]["channel"]
            message_ts = event["item"]["ts"]

            result = app.client.conversations_history(
                channel=channel_id,
                inclusive=True,
                oldest=message_ts,
                limit=1
            )
            message_text = (
                "The following was discovered at UTC "
                + message_ts + ": "
                + result["messages"][0]["text"]
            )

            stack_queue.put(message_text)
            logger.info(f"Message added to stack: {message_text}")

    except Exception as e:
        logger.error(f"Error handling reaction_added event: {e}")

if __name__ == "__main__":
    thread = threading.Thread(target=process_messages)
    thread.daemon = True
    thread.start()

    app.start(port=int(os.environ.get("PORT", 3000)))
