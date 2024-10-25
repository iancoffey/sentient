import os
from slack_bolt import App
from openai import OpenAI
from queue import LifoQueue
import threading
import tempfile
import logging
import time

logging.basicConfig(level=logging.INFO)

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)

thread = client.beta.threads.create()

assistant_name = os.environ.get("ASSISTANT_ID")
assistant = client.beta.assistants.retrieve(assistant_name)

stack_queue = LifoQueue()

def create_vector_store():
    """
    Creates a vector store using file resources for an assistant

    """

    vector_store = client.beta.vector_stores.create(name="security data")
    new_data = " ".join(stack_queue.queue).encode('utf-8')

    if not new_data:
        return

    with tempfile.NamedTemporaryFile(delete=True, suffix=".txt") as tmp:
        # Write combined data
        tmp.write(new_data)
        tmp.flush()

        file_resource = client.files.create(
            file=open(tmp.name, "rb"),
            purpose="assistants"
        )

        vector_store_file = client.beta.vector_stores.files.create(
            vector_store_id=vector_store.id,
            file_id=file_resource.id
        )

    client.beta.assistants.update(
        assistant_id=assistant.id,
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
    )

def task():
    while True:
        time.sleep(10)

        if stack_queue.qsize() == 0:
            continue

        create_vector_store()

@app.event("reaction_added")
def handle_reaction_added(body, say, logger):
    """
    Handles the 'reaction_added' event.

    Args:
        body: The request body containing event information.
        say: A function to send a message to the channel.
        logger: A logger to log information.
    """
    try:
        event = body["event"]
        reaction = event["reaction"]
        if reaction == "eyes":  # Check if the reaction is :eyes:
            channel_id = event["item"]["channel"]
            message_ts = event["item"]["ts"]

            # Fetch the message text using the Web API client
            result = app.client.conversations_history(
                channel=channel_id,
                inclusive=True,
                oldest=message_ts,
                limit=1
            )
            # TODO: prepend timestamp and date
            message_text = result["messages"][0]["text"]

            stack_queue.put(message_text)
            logger.info(f"Message added to stack: {message_text}")

    except Exception as e:
        logger.error(f"Error handling reaction_added event: {e}")

if __name__ == "__main__":
    thread = threading.Thread(target=task)
    thread.daemon = True
    thread.start()

    app.start(port=int(os.environ.get("PORT", 3000)))
