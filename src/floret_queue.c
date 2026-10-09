// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret_queue.c
//
//  Single-producer / single-consumer queue. head and tail are free-running
//  counters: the producer owns head, the consumer owns tail, so neither side
//  ever writes the other's index and no locking is needed. The release store
//  of an index publishes the slot contents written before it.
//------------------------------------------------------------------------------
#include <stddef.h>
#include <string.h>

#include "floret/floret_port.h"
#include "floret/floret_queue.h"
#include "floret_atomic.h"

static inline uint8_t* slot(const floret_queue_t* q, uint32_t index)
{
    return q->storage + (index & q->mask) * q->slot_size;
}

bool floret_queue_init(floret_queue_t* q, void* storage, uint32_t item_size,
                       uint32_t capacity, bool timestamped)
{
    if(q == NULL || storage == NULL || item_size == 0u)
        return false;
    if(capacity == 0u || (capacity & (capacity - 1u)) != 0u)
        return false;                                                           // power of two only

    q->storage     = (uint8_t*)storage;
    q->item_size   = item_size;
    q->slot_size   = FLORET_QUEUE_SLOT_SIZE(item_size, timestamped);
    q->mask        = capacity - 1u;
    q->timestamped = timestamped;
    q->dropped     = 0u;
    fa_store(&q->head, 0u);
    fa_store(&q->tail, 0u);
    return true;
}

bool floret_queue_put(floret_queue_t* q, const void* item)
{
    const uint32_t head = __atomic_load_n(&q->head, __ATOMIC_RELAXED);          // our own index
    const uint32_t tail = fa_load(&q->tail);
    if(head - tail > q->mask)
    {
        fa_inc(&q->dropped);                                                    // full
        return false;
    }

    uint8_t* s = slot(q, head);
    if(q->timestamped)
    {
        const uint32_t stamp = floret_port_now();
        memcpy(s, &stamp, sizeof(stamp));
        s += sizeof(stamp);
    }
    memcpy(s, item, q->item_size);
    fa_store(&q->head, head + 1u);                                              // publish
    return true;
}

bool floret_queue_get(floret_queue_t* q, void* item, uint32_t* stamp)
{
    const uint32_t tail = __atomic_load_n(&q->tail, __ATOMIC_RELAXED);          // our own index
    const uint32_t head = fa_load(&q->head);
    if(head == tail)
        return false;                                                           // empty

    const uint8_t* s = slot(q, tail);
    uint32_t       t = 0u;
    if(q->timestamped)
    {
        memcpy(&t, s, sizeof(t));
        s += sizeof(t);
    }
    if(item != NULL)
        memcpy(item, s, q->item_size);
    if(stamp != NULL)
        *stamp = t;
    fa_store(&q->tail, tail + 1u);                                              // free the slot
    return true;
}

uint32_t floret_queue_count(const floret_queue_t* q)
{
    const uint32_t tail = fa_load(&q->tail);
    const uint32_t head = fa_load(&q->head);
    return head - tail;
}

uint32_t floret_queue_dropped(const floret_queue_t* q)
{
    return fa_load(&q->dropped);
}
