import { NextResponse } from "next/server";
import { publicUrl } from "@/lib/basePath";
import { supabaseServer } from "@/lib/supabase/server";

export async function POST(request: Request) {
  const supabase = await supabaseServer();
  await supabase.auth.signOut();
  return NextResponse.redirect(publicUrl(request, "/login"), { status: 303 });
}
