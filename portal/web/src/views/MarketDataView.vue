<script setup lang="ts">
import { ref } from 'vue';
import MarketDataTicker from '@/components/MarketDataTicker.vue';

// List of instruments to display
const instruments = ref([
  'EUR/USD',
  'GBP/USD',
  'USD/JPY',
  'AUD/USD',
  'USD/CAD',
  'NZD/USD',
  'EUR/GBP',
  'EUR/JPY',
  'GBP/JPY',
  'AUD/JPY'
]);

const showAllInstruments = ref(false);
</script>

<template>
  <div class="market-data-view">
    <h1>Real-time Market Data</h1>
    
    <div class="controls">
      <button 
        @click="showAllInstruments = !showAllInstruments"
        class="toggle-button"
      >
        {{ showAllInstruments ? 'Show Less' : 'Show All Instruments' }}
      </button>
    </div>
    
    <div class="market-ticker-container">
      <MarketDataTicker 
        :instruments="instruments"
        :show-all-instruments="showAllInstruments"
        :max-instruments="5"
      />
    </div>
    
    <div class="market-data-content">
      <div class="info-panel">
        <h2>How to Use</h2>
        <p>
          This page displays real-time market data using WebSockets. The data is streamed directly from the OANDA API
          and updated in real-time.
        </p>
        <h3>Features:</h3>
        <ul>
          <li>Real-time price updates</li>
          <li>Automatic reconnection</li>
          <li>Responsive design</li>
          <li>Connection status indicator</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.market-data-view {
  max-width: 1200px;
  margin: 0 auto;
  padding: 1rem;
}

h1 {
  color: #2c3e50;
  margin-bottom: 1.5rem;
  text-align: center;
}

.controls {
  display: flex;
  justify-content: center;
  margin-bottom: 1.5rem;
}

.toggle-button {
  background-color: #3498db;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.9rem;
  transition: background-color 0.2s;
}

.toggle-button:hover {
  background-color: #2980b9;
}

.market-ticker-container {
  margin-bottom: 2rem;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.market-data-content {
  display: grid;
  grid-template-columns: 1fr;
  gap: 2rem;
}

.info-panel {
  background-color: #fff;
  border-radius: 8px;
  padding: 1.5rem;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.info-panel h2 {
  color: #2c3e50;
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1.5rem;
}

.info-panel h3 {
  color: #2c3e50;
  margin-top: 1.5rem;
  margin-bottom: 0.75rem;
  font-size: 1.2rem;
}

.info-panel p {
  color: #34495e;
  line-height: 1.6;
  margin-bottom: 1rem;
}

.info-panel ul {
  padding-left: 1.5rem;
  color: #34495e;
  line-height: 1.6;
}

.info-panel li {
  margin-bottom: 0.5rem;
}

/* Responsive design */
@media (min-width: 768px) {
  .market-data-content {
    grid-template-columns: 2fr 1fr;
  }
}
</style>
