import { job } from '@/lib/daten';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const eintrag = job(id);
    if (!eintrag) {
      return Response.json({ fehler: `Kein Job mit der id ${id}` }, { status: 404 });
    }
    return Response.json(eintrag);
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[jobs/${id}] ${text}`);
    return Response.json({ fehler: text }, { status: 500 });
  }
}
