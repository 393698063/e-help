export async function POST(request: Request) {
  const baseUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
  const body = await request.text();
  const response = await fetch(`${baseUrl}/api/chat/stream`, {
    method: "POST",
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
    },
    body,
  });

  return new Response(response.body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    },
  });
}

