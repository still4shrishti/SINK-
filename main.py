from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="lm-studio"
)

models = client.models.list()
model_id = models.data[0].id

print("SINK is online. Type 'exit' to quit.\n")

while True:
    user_input = input("You: ")

    if user_input.lower() == "exit":
        break

    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": "You are SINK, a helpful local AI assistant."},
            {"role": "user", "content": user_input}
        ]
    )

    print("SINK:", response.choices[0].message.content)