// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret_queue.h
//
//  Single-producer / single-consumer queue of fixed-size items, lock-free:
//  typically an interrupt puts and a task gets. Optional per-item timestamps
//  from floret_port_now(). See docs/design.md, section 10.
//
//  Usage:
//      static uint8_t storage[FLORET_QUEUE_STORAGE_SIZE(sizeof(midi_msg_t), 64, true)];
//      static floret_queue_t midi_q;
//      floret_queue_init(&midi_q, storage, sizeof(midi_msg_t), 64, true);
//
//      producer:  floret_queue_put(&midi_q, &msg);  floret_signal(midi, FLORET_ISR, MIDI_RX);
//      consumer:  while(floret_queue_get(&midi_q, &msg, &stamp)) { ... }
//------------------------------------------------------------------------------
#ifndef FLORET_QUEUE_H
#define FLORET_QUEUE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

//------------------------------------------------------------------------------
//  Storage layout: capacity slots. A timestamped slot starts with a 4-byte
//  stamp and its item is padded to a multiple of 4 bytes.
//------------------------------------------------------------------------------
#define FLORET_QUEUE_SLOT_SIZE(item_size, timestamped) \
    ((timestamped) ? (4u + ((((uint32_t)(item_size)) + 3u) & ~3u)) : ((uint32_t)(item_size)))

#define FLORET_QUEUE_STORAGE_SIZE(item_size, capacity, timestamped) \
    (((uint32_t)(capacity)) * FLORET_QUEUE_SLOT_SIZE(item_size, timestamped))

//------------------------------------------------------------------------------
//  Treat the fields as private. head is written only by the producer, tail
//  only by the consumer; both are free-running and accessed atomically.
//------------------------------------------------------------------------------
typedef struct {
    uint8_t* storage;                                                           // private
    uint32_t item_size;                                                         // private
    uint32_t slot_size;                                                         // private
    uint32_t mask;                                                              // private: capacity - 1
    uint32_t head;                                                              // private: producer index
    uint32_t tail;                                                              // private: consumer index
    uint32_t dropped;                                                           // private: puts refused because full
    bool     timestamped;                                                       // private
} floret_queue_t;

//------------------------------------------------------------------------------
//  Function: floret_queue_init
//      Prepare a queue. Call before either side uses it.
//
//  Parameters:
//      q           - queue
//      storage     - FLORET_QUEUE_STORAGE_SIZE(item_size, capacity,
//                    timestamped) bytes, 4-byte aligned
//      item_size   - bytes per item
//      capacity    - items; must be a power of two
//      timestamped - true to record floret_port_now() with each item
//
//  Returns:
//      true, or false if capacity is not a power of two
//------------------------------------------------------------------------------
bool floret_queue_init(floret_queue_t* q, void* storage, uint32_t item_size,
                       uint32_t capacity, bool timestamped);

//------------------------------------------------------------------------------
//  Function: floret_queue_put
//      Producer side: copy one item in. Never blocks.
//
//  Returns:
//      true, or false if the queue was full (the drop is counted)
//------------------------------------------------------------------------------
bool floret_queue_put(floret_queue_t* q, const void* item);

//------------------------------------------------------------------------------
//  Function: floret_queue_get
//      Consumer side: copy the oldest item out. Never blocks.
//
//  Parameters:
//      q     - queue
//      item  - receives the item
//      stamp - receives the timestamp (may be NULL; 0 if not timestamped)
//
//  Returns:
//      true, or false if the queue was empty
//------------------------------------------------------------------------------
bool floret_queue_get(floret_queue_t* q, void* item, uint32_t* stamp);

//------------------------------------------------------------------------------
//  Function: floret_queue_count
//      Items waiting. Exact for the consumer; a snapshot for anyone else.
//------------------------------------------------------------------------------
uint32_t floret_queue_count(const floret_queue_t* q);

//------------------------------------------------------------------------------
//  Function: floret_queue_dropped
//      Puts refused because the queue was full, since init.
//------------------------------------------------------------------------------
uint32_t floret_queue_dropped(const floret_queue_t* q);

#ifdef __cplusplus
}
#endif

#endif // FLORET_QUEUE_H
