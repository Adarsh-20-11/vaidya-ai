'use client';

import { useState, useRef, useEffect } from 'react';

// ── Types ──
type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  toolsUsed?: string[];
};

type StockAlert = {
  code: string;
  name: string;
  urgency: 'critical' | 'watch' | 'anomaly';
  days_remaining: number | null;
  closing_stock: number;
  default_supplier: string | null;
};

type Anomaly = {
  item_code: string;
  item_name: string;
  anomaly_type: string;
  severity: string;
  detail: string;
};

// ── Utility ──
const uid = () => Math.random().toString(36).slice(2);
const formatTime = (d: Date) =>
  d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });

// ── Severity styling ──
const URGENCY_STYLE: Record<string, string> = {
  critical: 'bg-red-500/15 border-red-500/40 text-red-300',
  watch:    'bg-amber-500/15 border-amber-500/40 text-amber-300',
  anomaly:  'bg-purple-500/15 border-purple-500/40 text-purple-300',
  ok:       'bg-emerald-500/15 border-emerald-500/40 text-emerald-300',
};

const SEVERITY_DOT: Record<string, string> = {
  critical: 'bg-red-400',
  high:     'bg-orange-400',
  medium:   'bg-amber-400',
  low:      'bg-slate-400',
};

// ── Quick action prompts ──
const QUICK_ACTIONS = [
  { label: 'Aaj kya urgent hai?', icon: '🚨' },
  { label: 'Kaunsa stock khatam ho raha hai?', icon: '📦' },
  { label: 'Margin problem kahan hai?', icon: '📉' },
  { label: 'Dead stock dikhao', icon: '⚰️' },
  { label: 'Aaj ka brief chahiye', icon: '📋' },
  { label: 'Supplier rates ka trend?', icon: '📈' },
];

export default function VaidyaDashboard() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: `Namaste! Main Vaidya-AI hoon — Magadh Wellness ka business assistant. 🙏\n\nAaj main aapki kya madad kar sakta hoon?\n\nNeecha diye quick actions use kar sakte hain, ya seedha puchh sakte hain.`,
      timestamp: new Date(),
    }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'chat' | 'dashboard'>('chat');
  const [alerts, setAlerts] = useState<StockAlert[]>([]);
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [pipelineStatus, setPipelineStatus] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const loadDashboard = async () => {
    setDashboardLoading(true);
    try {
      const res = await fetch('/api/dashboard');
      const data = await res.json();
      setAlerts(data.alerts || []);
      setAnomalies(data.anomalies || []);
      setPipelineStatus(data.pipeline_status || '');
    } catch (e) {
      console.error('Dashboard load failed', e);
    } finally {
      setDashboardLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'dashboard') loadDashboard();
  }, [activeTab]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return;

    const userMsg: Message = {
      id: uid(), role: 'user',
      content: text, timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [...messages, userMsg].map(m => ({
            role: m.role, content: m.content
          }))
        }),
      });

      const data = await res.json();

      const assistantMsg: Message = {
        id: uid(), role: 'assistant',
        content: data.content || 'Kuch galat ho gaya. Phir se try karein.',
        timestamp: new Date(),
        toolsUsed: data.tools_used || [],
      };
      setMessages(prev => [...prev, assistantMsg]);
    } catch (e) {
      setMessages(prev => [...prev, {
        id: uid(), role: 'assistant',
        content: '⚠️ Server se connect nahi ho paya. Please try again.',
        timestamp: new Date(),
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-slate-100 font-sans flex flex-col"
         style={{ fontFamily: "'DM Sans', system-ui, sans-serif" }}>

      {/* ── Header ── */}
      <header className="border-b border-white/8 bg-[#0d0d14]/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600
                            flex items-center justify-center text-lg shadow-lg shadow-emerald-900/40">
              ⚕️
            </div>
            <div>
              <h1 className="text-base font-semibold tracking-tight text-white">Vaidya-AI</h1>
              <p className="text-[11px] text-slate-500">Magadh Wellness • Business Assistant</p>
            </div>
          </div>

          {/* Tab switcher */}
          <div className="flex bg-white/5 rounded-xl p-1 gap-1">
            {(['chat', 'dashboard'] as const).map(tab => (
              <button key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${
                  activeTab === tab
                    ? 'bg-emerald-500/20 text-emerald-300 shadow-sm'
                    : 'text-slate-500 hover:text-slate-300'
                }`}>
                {tab === 'chat' ? '💬 Chat' : '📊 Dashboard'}
              </button>
            ))}
          </div>

          {/* Status indicator */}
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            Online
          </div>
        </div>
      </header>

      {/* ── Main Content ── */}
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-6">

        {/* ══ CHAT TAB ══ */}
        {activeTab === 'chat' && (
          <div className="flex flex-col h-[calc(100vh-140px)]">

            {/* Messages */}
            <div className="flex-1 overflow-y-auto space-y-4 pr-2 pb-4 scrollbar-thin
                            scrollbar-thumb-white/10 scrollbar-track-transparent">
              {messages.map(msg => (
                <div key={msg.id}
                     className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[80%] ${msg.role === 'user' ? 'order-last' : ''}`}>

                    {/* Avatar */}
                    {msg.role === 'assistant' && (
                      <div className="flex items-center gap-2 mb-1.5">
                        <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-emerald-400
                                        to-teal-600 flex items-center justify-center text-xs">
                          ⚕️
                        </div>
                        <span className="text-[11px] text-slate-500">Vaidya-AI</span>
                        <span className="text-[11px] text-slate-600">{formatTime(msg.timestamp)}</span>
                      </div>
                    )}

                    {/* Bubble */}
                    <div className={`rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
                      msg.role === 'user'
                        ? 'bg-emerald-500/20 border border-emerald-500/30 text-emerald-50 rounded-tr-sm'
                        : 'bg-white/5 border border-white/8 text-slate-200 rounded-tl-sm'
                    }`}>
                      {msg.content}
                    </div>

                    {/* Tool badges */}
                    {msg.toolsUsed && msg.toolsUsed.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {msg.toolsUsed.map(tool => (
                          <span key={tool}
                            className="text-[10px] px-2 py-0.5 rounded-full bg-slate-800
                                       border border-white/8 text-slate-500 font-mono">
                            ⚙ {tool}
                          </span>
                        ))}
                      </div>
                    )}

                    {msg.role === 'user' && (
                      <div className="text-right mt-1">
                        <span className="text-[11px] text-slate-600">{formatTime(msg.timestamp)}</span>
                      </div>
                    )}
                  </div>
                </div>
              ))}

              {/* Typing indicator */}
              {loading && (
                <div className="flex justify-start">
                  <div className="bg-white/5 border border-white/8 rounded-2xl rounded-tl-sm px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      {[0,1,2].map(i => (
                        <span key={i}
                          className="w-2 h-2 rounded-full bg-emerald-400/60 animate-bounce"
                          style={{ animationDelay: `${i * 150}ms` }} />
                      ))}
                      <span className="text-xs text-slate-500 ml-1">Soch raha hoon...</span>
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Quick actions */}
            {messages.length <= 1 && (
              <div className="grid grid-cols-3 gap-2 mb-4">
                {QUICK_ACTIONS.map(action => (
                  <button key={action.label}
                    onClick={() => sendMessage(action.label)}
                    className="flex items-center gap-2 px-3 py-2.5 rounded-xl bg-white/4
                               border border-white/8 text-left text-xs text-slate-400
                               hover:bg-white/8 hover:text-slate-200 hover:border-emerald-500/30
                               transition-all duration-200 group">
                    <span className="text-base">{action.icon}</span>
                    <span className="leading-tight">{action.label}</span>
                  </button>
                ))}
              </div>
            )}

            {/* Input area */}
            <div className="border border-white/10 rounded-2xl bg-white/4 backdrop-blur
                            focus-within:border-emerald-500/40 transition-colors duration-200">
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Kuch bhi puchho... (Enter to send, Shift+Enter for new line)"
                rows={1}
                className="w-full bg-transparent px-4 pt-3 pb-1 text-sm text-slate-200
                           placeholder-slate-600 resize-none outline-none
                           max-h-32 min-h-[44px]"
                style={{ fieldSizing: 'content' } as React.CSSProperties}
                disabled={loading}
              />
              <div className="flex items-center justify-between px-4 pb-3">
                <span className="text-[11px] text-slate-600">
                  {input.length > 0 ? `${input.length} chars` : 'Hinglish, Hindi, or English'}
                </span>
                <button
                  onClick={() => sendMessage(input)}
                  disabled={!input.trim() || loading}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                             bg-emerald-500/20 border border-emerald-500/30 text-emerald-300
                             hover:bg-emerald-500/30 disabled:opacity-30 disabled:cursor-not-allowed
                             transition-all duration-200">
                  Send ↵
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ══ DASHBOARD TAB ══ */}
        {activeTab === 'dashboard' && (
          <div className="space-y-6">

            {/* Refresh button */}
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white">Stock Intelligence</h2>
              <button onClick={loadDashboard}
                disabled={dashboardLoading}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs
                           border border-white/10 text-slate-400 hover:text-slate-200
                           hover:border-white/20 transition-all disabled:opacity-40">
                {dashboardLoading ? '⟳ Loading...' : '↻ Refresh'}
              </button>
            </div>

            {dashboardLoading ? (
              <div className="flex items-center justify-center h-40">
                <div className="flex items-center gap-3 text-slate-500">
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                  Data load ho raha hai...
                </div>
              </div>
            ) : (
              <>
                {/* Anomalies */}
                {anomalies.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-slate-400 mb-3 flex items-center gap-2">
                      <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
                      Aaj ke Issues ({anomalies.length})
                    </h3>
                    <div className="grid gap-2">
                      {anomalies.slice(0, 8).map((a, i) => (
                        <div key={i}
                          className="flex items-start gap-3 p-3 rounded-xl bg-white/3
                                     border border-white/8 hover:bg-white/5 transition-colors">
                          <span className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
                            SEVERITY_DOT[a.severity] || 'bg-slate-400'
                          }`} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-sm font-medium text-slate-200 truncate">
                                {a.item_name || a.item_code}
                              </span>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/8
                                             text-slate-500 font-mono flex-shrink-0">
                                {a.anomaly_type.replace(/_/g, ' ')}
                              </span>
                            </div>
                            <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
                              {a.detail}
                            </p>
                          </div>
                          <button
                            onClick={() => {
                              setActiveTab('chat');
                              setTimeout(() => sendMessage(
                                `${a.item_name} ke baare mein batao — ${a.anomaly_type.replace(/_/g,' ')} issue hai`
                              ), 100);
                            }}
                            className="text-[11px] text-emerald-400/60 hover:text-emerald-300
                                       flex-shrink-0 hover:underline transition-colors">
                            Ask AI →
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Stock alerts */}
                {alerts.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-slate-400 mb-3 flex items-center gap-2">
                      <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                      Stock Alerts
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {alerts.map((a, i) => (
                        <div key={i}
                          className={`p-3 rounded-xl border ${URGENCY_STYLE[a.urgency] || URGENCY_STYLE.ok}
                                     hover:opacity-80 transition-opacity cursor-pointer`}
                          onClick={() => {
                            setActiveTab('chat');
                            setTimeout(() => sendMessage(
                              `${a.name} ka stock status batao`
                            ), 100);
                          }}>
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium truncate">{a.name}</p>
                              <p className="text-xs opacity-70 mt-0.5">
                                {a.closing_stock} units
                                {a.days_remaining
                                  ? ` • ${a.days_remaining} din baaki`
                                  : ' • velocity unknown'}
                              </p>
                            </div>
                            <span className="text-xs font-semibold uppercase tracking-wide
                                           opacity-80 flex-shrink-0">
                              {a.urgency}
                            </span>
                          </div>
                          {a.default_supplier && (
                            <p className="text-[11px] opacity-60 mt-1.5">
                              Supplier: {a.default_supplier}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Empty state */}
                {alerts.length === 0 && anomalies.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-48 gap-3">
                    <span className="text-4xl">✅</span>
                    <p className="text-slate-400 text-sm">Aaj koi urgent issue nahi hai</p>
                    <button onClick={() => setActiveTab('chat')}
                      className="text-xs text-emerald-400 hover:underline">
                      Chat mein kuch puchho →
                    </button>
                  </div>
                )}

                {/* Pipeline status */}
                {pipelineStatus && (
                  <div className="p-4 rounded-xl bg-white/3 border border-white/8">
                    <h3 className="text-xs font-medium text-slate-500 mb-2">Pipeline Status</h3>
                    <pre className="text-[11px] text-slate-500 leading-relaxed whitespace-pre-wrap
                                    font-mono">
                      {pipelineStatus}
                    </pre>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
