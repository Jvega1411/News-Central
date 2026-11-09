'use strict';
import { initReader } from './reader.js';
import { initSpine } from './spine.js';
import { initMap } from './map.js';

initReader(document);
initSpine(document.getElementById('spine'));
initMap(document.getElementById('map'));
