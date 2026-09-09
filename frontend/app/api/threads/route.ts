export async function POST() {
  const baseUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
  const response = await fetch(`${baseUrl}/api/threads`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({}),
  });
  const data = await response.text();
  return new Response(data, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

