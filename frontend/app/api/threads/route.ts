export async function POST(request: Request) {
  const baseUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
  const body = await request.text();
  const response = await fetch(`${baseUrl}/api/threads`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body,
  });
  const data = await response.text();
  return new Response(data, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}
