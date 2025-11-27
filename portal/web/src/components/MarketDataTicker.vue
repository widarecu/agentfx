<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { useMarketDataWebSocket, type PriceUpdate } from '@/services/marketDataWebSocket';

const props = defineProps({
  instruments: {
    type: Array as () => string[],
    required: true,
  },
  showAllInstruments: {
    type: Boolean,
    default: false,
  },
  maxInstruments: {
    type: Number,
    default: 5,
  },
});

const { marketDataWebSocket, isConnected, error } = useMarketDataWebSocket();

// Track latest prices for each instrument
const prices = ref<Record<string, PriceUpdate>>({});

// Handle price updates
const handlePriceUpdate = (data: any) => {
  if (data.type === 'price_update') {
    prices.value = {
      ...prices.value,
      [data.instrument]: data,
    };
  }
};

// Subscribe to instruments when component is mounted
onMounted(() => {
  marketDataWebSocket.addMessageHandler(handlePriceUpdate);
  
  // Subscribe to each instrument
  const instrumentsToSubscribe = props.showAllInstruments 
    ? props.instruments 
    : props.instruments.slice(0, props.maxInstruments);
    
  instrumentsToSubscribe.forEach((instrument) => {
    marketDataWebSocket.subscribe(instrument);
  });
});

// Clean up on component unmount
onUnmounted(() => {
  marketDataWebSocket.removeMessageHandler(handlePriceUpdate);
  
  // Unsubscribe from all instruments
  Object.keys(prices.value).forEach((instrument) => {
    marketDataWebSocket.unsubscribe(instrument);
  });
});

// Format price with proper decimal places
const formatPrice = (price: number, instrument: string) => {
  // Determine decimal places based on currency pair
  const isForexMajor = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD', 'NZD/USD'].includes(instrument);
  const decimalPlaces = isForexMajor ? 5 : 3;
  return price.toFixed(decimalPlaces);
};

// Calculate price change percentage and color
const getPriceChangeClass = (price: PriceUpdate) => {
  if (!price || !price.mid) return '';
  
  // This is a simplified example - you might want to track previous prices
  // to determine if the price went up or down
  return 'text-gray-800'; // Default color
};
</script>

<template>
  <div class="market-ticker">
    <div v-if="error" class="error-message">
      {{ error }}
    </div>
    
    <div v-else class="ticker-container">
      <div class="connection-status" :class="{ 'connected': isConnected, 'disconnected': !isConnected }">
        {{ isConnected ? '●' : '○' }}
      </div>
      
      <div class="ticker-items">
        <div 
          v-for="instrument in Object.keys(prices)" 
          :key="instrument"
          class="ticker-item"
          :class="getPriceChangeClass(prices[instrument])"
        >
          <span class="instrument">{{ instrument }}</span>
          <span class="price">
            {{ prices[instrument] ? formatPrice(prices[instrument].mid, instrument) : '--' }}
          </span>
          <span class="spread" v-if="prices[instrument]">
            {{ formatPrice(prices[instrument].spread, instrument) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.market-ticker {
  background-color: #f8f9fa;
  border: 1px solid #e9ecef;
  border-radius: 4px;
  padding: 0.5rem;
  font-family: 'Roboto', sans-serif;
  overflow: hidden;
}

.ticker-container {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.connection-status {
  font-size: 0.8rem;
  margin-right: 0.5rem;
}

.connected {
  color: #28a745;
}

.disconnected {
  color: #dc3545;
}

.ticker-items {
  display: flex;
  gap: 2rem;
  overflow-x: auto;
  scrollbar-width: none; /* Hide scrollbar for Firefox */
  -ms-overflow-style: none; /* Hide scrollbar for IE and Edge */
}

.ticker-items::-webkit-scrollbar {
  display: none; /* Hide scrollbar for Chrome, Safari and Opera */
}

.ticker-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  white-space: nowrap;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  background-color: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.instrument {
  font-weight: 600;
  color: #343a40;
}

.price {
  font-family: 'Roboto Mono', monospace;
  font-weight: 500;
}

.spread {
  font-size: 0.8em;
  color: #6c757d;
  font-family: 'Roboto Mono', monospace;
}

/* Price change colors */
.text-up {
  color: #28a745;
}

.text-down {
  color: #dc3545;
}

.error-message {
  color: #dc3545;
  padding: 0.5rem;
  text-align: center;
  font-weight: 500;
}
</style>
