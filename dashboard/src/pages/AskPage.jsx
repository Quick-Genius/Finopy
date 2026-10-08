import { useState } from 'react';
import { Send, Sparkles, Lightbulb, MessageSquare } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { askQuestion } from '../api/client';

const SUGGESTIONS = [
  'Why did my AWS bill spike this week?',
  'Which services are costing the most?',
  'Show me idle EC2 instances',
  'How can I reduce RDS costs?',
  'Forecast next month\'s spend',
];

export default function AskPage() {
  const [query, setQuery] = useState('');
  const [answer, setAnswer] = useState(null);
  const [loading, setLoading] = useState(false);
  const [suggestions, setSuggestions] = useState([]);

  const handleAsk = async (q) => {
    const question = q || query;
    if (!question.trim()) return;

    setLoading(true);
    setAnswer(null);
    try {
      const { data } = await askQuestion(question);
      setAnswer(data.answer);
      setSuggestions(data.follow_up_suggestions || []);
    } catch (err) {
      setAnswer(`Error: ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <MessageSquare size={22} color="var(--accent)" />
          Ask About Your Cloud Costs
        </h2>
        <p>Get instant AI-powered insights grounded in your actual billing data</p>
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="input-group">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAsk()}
            placeholder="Ask anything about your cloud costs..."
          />
        </div>
        <button className="btn btn-primary" onClick={() => handleAsk()} disabled={loading}>
          <Send size={16} />
          {loading ? 'Analyzing...' : 'Ask'}
        </button>
      </div>

      {!answer && !loading && (
        <div className="card">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Lightbulb size={18} color="var(--accent-orange)" />
            Try asking...
          </h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                className="btn btn-outline"
                onClick={() => { setQuery(s); handleAsk(s); }}
                style={{ fontSize: 13 }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {loading && (
        <div className="loading-spinner">Agents analyzing your question...</div>
      )}

      {answer && (
        <div className="card animate-fade-in" style={{ marginTop: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Sparkles size={18} color="var(--accent)" />
            <h3>AI Response</h3>
          </div>
          <div className="report-content"><ReactMarkdown>{answer}</ReactMarkdown></div>

          {suggestions.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                Follow-up questions:
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {suggestions.map((s) => (
                  <button
                    key={s}
                    className="btn btn-outline"
                    onClick={() => { setQuery(s); handleAsk(s); }}
                    style={{ fontSize: 12 }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
