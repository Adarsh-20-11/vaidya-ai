/**
 * app/api/dashboard/route.ts
 *
 * Dashboard data endpoint. Fetches stock alerts, anomalies, and
 * pipeline status directly from Supabase (read-only).
 *
 * Called by the dashboard tab in page.tsx.
 * Does NOT use MCP — direct DB reads are faster for static dashboard data.
 */

import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

const supabase = createClient(
  process.env.SUPABASE_URL || '',
  process.env.SUPABASE_KEY || ''
);

export async function GET() {
  try {
    const today = new Date().toISOString().split('T')[0];

    // Stock alerts — critical and watch items
    const { data: alertsData } = await supabase
      .from('v_item_dashboard')
      .select('code, name, reorder_urgency, days_remaining, closing_stock, default_supplier')
      .in('reorder_urgency', ['critical', 'watch', 'anomaly'])
      .order('reorder_urgency')
      .limit(20);

    // Anomalies for today
    const { data: anomaliesData } = await supabase
      .from('anomalies_today')
      .select('item_code, item_name, anomaly_type, severity, detail')
      .eq('detected_date', today)
      .eq('resolved', false)
      .order('severity')
      .limit(15);

    // Pipeline status — latest runs per report type
    const { data: pipelineRuns } = await supabase
      .from('pipeline_runs')
      .select('report_id, success, row_count, ran_at')
      .order('ran_at', { ascending: false })
      .limit(10);

    // Format pipeline status as readable text
    const seenReports = new Set<string>();
    const statusLines: string[] = [];
    for (const run of pipelineRuns || []) {
      if (seenReports.has(run.report_id)) continue;
      seenReports.add(run.report_id);
      const icon = run.success ? '✅' : '❌';
      const date = new Date(run.ran_at).toLocaleString('en-IN');
      statusLines.push(`${icon} ${run.report_id}: ${run.row_count} rows | ${date}`);
    }

    // Map alerts to expected shape
    const alerts = (alertsData || []).map((a: {
      code: string;
      name: string;
      reorder_urgency: string;
      days_remaining: number | null;
      closing_stock: number;
      default_supplier: string | null;
    }) => ({
      code: a.code,
      name: a.name,
      urgency: a.reorder_urgency,
      days_remaining: a.days_remaining,
      closing_stock: a.closing_stock,
      default_supplier: a.default_supplier,
    }));

    return NextResponse.json({
      alerts,
      anomalies: anomaliesData || [],
      pipeline_status: statusLines.join('\n') || 'No pipeline runs yet.',
      as_of: today,
    });

  } catch (error) {
    console.error('Dashboard API error:', error);
    return NextResponse.json(
      {
        alerts: [],
        anomalies: [],
        pipeline_status: 'Error loading dashboard data',
        error: String(error),
      },
      { status: 500 }
    );
  }
}
