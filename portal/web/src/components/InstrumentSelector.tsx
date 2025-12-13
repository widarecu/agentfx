import React, { useEffect, useState } from 'react';
import { ChevronDown, TrendingUp, AlertCircle } from 'lucide-react';
import { marketDataService, Instrument } from '../services/marketData';

interface InstrumentSelectorProps {
  value: string;
  onChange: (instrument: string) => void;
  disabled?: boolean;
}

export default function InstrumentSelector({ value, onChange, disabled = false }: InstrumentSelectorProps) {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    loadInstruments();
  }, []);

  const loadInstruments = async () => {
    try {
      setError(null);
      const data = await marketDataService.getInstruments();
      setInstruments(data);
      // If no value is selected and we have instruments, select the first one
      if (!value && data.length > 0) {
        onChange(data[0].symbol);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load instruments');
    } finally {
      setIsLoading(false);
    }
  };

  const selectedInstrument = instruments.find(i => i.symbol === value);

  if (isLoading) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2">
        <div className="animate-pulse flex items-center gap-2">
          <div className="h-5 w-5 bg-slate-200 dark:bg-slate-700 rounded"></div>
          <div className="h-4 bg-slate-200 dark:bg-slate-700 rounded w-32"></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-red-200 dark:border-red-800 rounded-lg px-4 py-2">
        <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
        className={`
          w-full bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700
          rounded-lg px-4 py-2.5 flex items-center justify-between gap-2
          transition-colors hover:bg-slate-50 dark:hover:bg-slate-700
          ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
          ${isOpen ? 'ring-2 ring-blue-500 dark:ring-blue-400' : ''}
        `}
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <TrendingUp className="h-4 w-4 text-slate-400 dark:text-slate-500 flex-shrink-0" />
          <div className="flex flex-col items-start min-w-0">
            <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {selectedInstrument?.symbol || 'Select instrument'}
            </span>
            {selectedInstrument && (
              <span className="text-xs text-slate-500 dark:text-slate-400 truncate max-w-full">
                {selectedInstrument.name}
              </span>
            )}
          </div>
        </div>
        <ChevronDown
          className={`h-4 w-4 text-slate-400 dark:text-slate-500 flex-shrink-0 transition-transform ${
            isOpen ? 'rotate-180' : ''
          }`}
        />
      </button>

      {isOpen && !disabled && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-10"
            onClick={() => setIsOpen(false)}
          />

          {/* Dropdown */}
          <div className="absolute z-20 mt-2 w-full bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg max-h-80 overflow-y-auto">
            {instruments.length === 0 ? (
              <div className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400 text-center">
                No instruments available
              </div>
            ) : (
              <div className="py-1">
                {instruments.map((instrument) => (
                  <button
                    key={instrument.symbol}
                    type="button"
                    onClick={() => {
                      onChange(instrument.symbol);
                      setIsOpen(false);
                    }}
                    className={`
                      w-full px-4 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-700
                      transition-colors flex flex-col gap-0.5
                      ${value === instrument.symbol ? 'bg-blue-50 dark:bg-blue-900/20' : ''}
                    `}
                  >
                    <span className={`text-sm font-medium ${
                      value === instrument.symbol
                        ? 'text-blue-600 dark:text-blue-400'
                        : 'text-slate-900 dark:text-slate-100'
                    }`}>
                      {instrument.symbol}
                    </span>
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      {instrument.name} • {instrument.type}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
