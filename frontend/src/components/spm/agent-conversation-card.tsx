// ─────────────────────────────────────────────────────────────────────
// AgentConversationCard — presentational demo card ported from AI-SPM.
// Renders a mock agent/employee chat transcript; the autoPlay reveal
// animation is purely cosmetic and carries no real data.
// ─────────────────────────────────────────────────────────────────────
'use client'
import { useState, useEffect } from 'react'
import { MessageSquare, Mail, Globe } from 'lucide-react'
import type { AgentConversation } from '@/lib/aispm/discovery'

interface AgentConversationCardProps {
  conversation: AgentConversation
  autoPlay?: boolean
  delay?: number
}

const CHANNEL_ICON = {
  slack: MessageSquare,
  email: Mail,
  web: Globe,
}
const STATUS_STYLE = {
  complete: 'bg-green-100 text-green-700',
  'in-progress': 'bg-blue-100 text-blue-700',
  'no-response': 'bg-slate-100 text-slate-500',
}
const STATUS_LABEL = {
  complete: 'Complete',
  'in-progress': 'In progress',
  'no-response': 'No response',
}

export function AgentConversationCard({ conversation, autoPlay = false, delay = 0 }: AgentConversationCardProps) {
  const [visibleCount, setVisibleCount] = useState(autoPlay ? 0 : conversation.messages.length)

  useEffect(() => {
    if (!autoPlay) return
    const timeout = setTimeout(() => {
      const interval = setInterval(() => {
        setVisibleCount(c => {
          if (c >= conversation.messages.length) {
            clearInterval(interval)
            return c
          }
          return c + 1
        })
      }, 1200)
      return () => clearInterval(interval)
    }, delay)
    return () => clearTimeout(timeout)
  }, [autoPlay, delay, conversation.messages.length])

  const visibleMessages = conversation.messages.slice(0, visibleCount)
  const lastExtracted = visibleMessages.findLast(m => m.extracted)
  const ChannelIcon = CHANNEL_ICON[conversation.channel]

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ChannelIcon size={14} className="text-slate-400 flex-shrink-0" aria-hidden="true" />
          <div>
            <div className="text-sm font-semibold text-slate-800">{conversation.employeeName}</div>
            <div className="text-xs text-slate-500">{conversation.department}</div>
          </div>
        </div>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${STATUS_STYLE[conversation.status]}`}>
          {STATUS_LABEL[conversation.status]}
        </span>
      </div>

      {/* Messages */}
      <div className="space-y-2">
        {visibleMessages.map((msg) => (
          <div key={msg.timestamp} className={`flex gap-2 ${msg.role === 'agent' ? '' : 'flex-row-reverse'}`}>
            <div aria-hidden="true" className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${
              msg.role === 'agent' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-600'
            }`}>
              {msg.role === 'agent' ? 'A' : conversation.employeeName[0]}
            </div>
            <div className={`text-xs px-3 py-2 rounded-xl max-w-xs leading-relaxed ${
              msg.role === 'agent'
                ? 'bg-indigo-50 text-indigo-800 rounded-tl-none'
                : 'bg-slate-100 text-slate-700 rounded-tr-none'
            }`}>
              {msg.text}
            </div>
          </div>
        ))}

        {/* Extracted badge */}
        {lastExtracted?.extracted && (
          <div className="mt-2 text-[10px] font-medium text-green-700 bg-green-50 border border-green-100 rounded-lg px-3 py-1.5">
            ✓ {lastExtracted.extracted}
          </div>
        )}
      </div>
    </div>
  )
}
