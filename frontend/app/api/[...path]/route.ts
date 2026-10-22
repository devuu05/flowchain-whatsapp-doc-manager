import { proxyRequest } from "@/lib/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type Ctx = { params: Promise<{ path: string[] }> };

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  return proxyRequest(req, path);
}

export { handle as DELETE, handle as GET, handle as HEAD, handle as PATCH, handle as POST, handle as PUT };
