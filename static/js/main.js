'use strict';
import { initReader } from './reader.js';
import { initSpine } from './spine.js';

initReader(document);
initSpine(document.getElementById('spine'));
