"""Round-based journey search over bus patterns, with times from the travel-time model.

Round k finds the earliest expected arrival at every stop using exactly k buses.
Two things differ from a timetable planner:

* There is no trusted timetable to look trips up in. A pattern is boarded after
  an expected wait that follows from how often its buses were really seen, and
  the ride is priced stretch by stretch at the time the bus would be there.
* Several routes usually cover the same stretch. A rider at stop A going to
  stop B takes whichever useful bus comes first, so lines are pooled per (A, B):
  starting from the fastest, a line joins the set while its ride is shorter than
  the current expected (wait + ride). Wait = alpha x the pooled headway.
"""
from __future__ import annotations

import numpy as np
from numba import njit

from ..calendar import BIN_S, IST_OFFSET_S, N_BINS

INF = np.inf
MAX_SINGLE_WAIT_S = 900.0   # only used to place the ride on the clock while searching
MIN_FREQ = 0.05             # fewer than one bus in 20 hours: not offered


@njit(cache=True)
def _lookup(est, key, dtp, t):
    pos = ((t + IST_OFFSET_S) % 86400.0) / BIN_S - 0.5
    b0 = int(np.floor(pos))
    w = pos - b0
    b1 = b0 + 1
    if b0 < 0:
        b0 += N_BINS
    if b1 >= N_BINS:
        b1 -= N_BINS
    return est[key, dtp, b0] * (1.0 - w) + est[key, dtp, b1] * w


@njit(cache=True)
def search(t0, dtp, acc_stop, acc_s, egr_stop, egr_s, rounds, alpha_s,
           ps_off, ps_stop, ps_seg, ps_run_m, sp_off, sp_pat, sp_seq, freq, banned,
           fp_off, fp_to, fp_s, run_est, dwell_est, lf, edges,
           tau, par_stop, par_walk, par_pat, par_bseq, par_aseq, par_wait, par_ride, dest_time, dest_stop):
    """Fills tau[k, stop] and the parent arrays; dest_time[k] / dest_stop[k] give the best finish using k buses.

    lf[h, stretch] multiplies a stretch's run time when the bus reaches it in look-ahead band h after t0
    (bands split at 'edges' seconds). One row of ones and no edges means "a typical day".
    """
    n_stops = tau.shape[1]
    cap = 200000
    e_stop = np.empty(cap, dtype=np.int64)
    e_f = np.empty(cap)
    e_ride = np.empty(cap)
    e_pat = np.empty(cap, dtype=np.int64)
    e_aseq = np.empty(cap, dtype=np.int64)
    e_bseq = np.empty(cap, dtype=np.int64)
    best = np.full(n_stops, INF)
    egr = np.full(n_stops, INF)
    for i in range(len(egr_stop)):
        egr[egr_stop[i]] = egr_s[i]
    marked = np.empty(n_stops, dtype=np.int64)
    is_marked = np.zeros(n_stops, dtype=np.bool_)
    nxt = np.empty(n_stops, dtype=np.int64)
    bus_time = np.empty(n_stops)
    n_marked = 0
    for i in range(len(acc_stop)):
        s = acc_stop[i]
        t = t0 + acc_s[i]
        if t < tau[0, s]:
            if tau[0, s] == INF:
                marked[n_marked] = s
                n_marked += 1
            tau[0, s] = t
            best[s] = t
    best_dest = INF
    for k in range(1, rounds + 1):
        n_next = 0
        is_marked[:] = False
        for mi in range(n_marked):
            a_stop = marked[mi]
            t_a = tau[k - 1, a_stop]
            if t_a >= best_dest:
                continue
            hour = int(((t_a + IST_OFFSET_S) % 86400.0) // 3600)
            ne = 0
            for q in range(sp_off[a_stop], sp_off[a_stop + 1]):
                p = sp_pat[q]
                if banned[p]:
                    continue
                f = freq[p, dtp, hour]
                if f < MIN_FREQ:
                    continue
                i_board = sp_seq[q]
                lo = ps_off[p]
                n_p = ps_off[p + 1] - lo
                board = t_a + min(alpha_s / f, MAX_SINGLE_WAIT_S)
                ride = 0.0
                for j in range(i_board, n_p - 1):
                    row = lo + j
                    if ps_seg[row] >= 0 and ps_run_m[row] > 0.0:
                        h = 0
                        while h < len(edges) and board + ride - t0 >= edges[h]:
                            h += 1
                        ride += _lookup(run_est, ps_seg[row], dtp, board + ride) * ps_run_m[row] * lf[h, ps_seg[row]]
                    if t_a + ride >= best_dest or ne >= cap:
                        break
                    b_stop = ps_stop[row + 1]
                    e_stop[ne] = b_stop
                    e_f[ne] = f
                    e_ride[ne] = ride
                    e_pat[ne] = p
                    e_aseq[ne] = j + 1
                    e_bseq[ne] = i_board
                    ne += 1
                    ride += _lookup(dwell_est, b_stop, dtp, board + ride)
            if ne == 0:
                continue
            order = np.argsort(e_stop[:ne] * 1.0e7 + e_ride[:ne])
            g = 0
            while g < ne:
                b_stop = e_stop[order[g]]
                first = order[g]
                f_sum = 0.0
                ft_sum = 0.0
                expect = INF
                h = g
                while h < ne and e_stop[order[h]] == b_stop:
                    e = order[h]
                    if e_ride[e] < expect:
                        f_sum += e_f[e]
                        ft_sum += e_f[e] * e_ride[e]
                        expect = (alpha_s + ft_sum) / f_sum
                    h += 1
                g = h
                cand = t_a + expect
                if cand < best[b_stop] and cand < best_dest:
                    tau[k, b_stop] = cand
                    best[b_stop] = cand
                    par_stop[k, b_stop] = a_stop
                    par_walk[k, b_stop] = -1
                    par_pat[k, b_stop] = e_pat[first]
                    par_bseq[k, b_stop] = e_bseq[first]
                    par_aseq[k, b_stop] = e_aseq[first]
                    par_wait[k, b_stop] = alpha_s / f_sum
                    par_ride[k, b_stop] = ft_sum / f_sum
                    if not is_marked[b_stop]:
                        is_marked[b_stop] = True
                        nxt[n_next] = b_stop
                        n_next += 1
                    if cand + egr[b_stop] < best_dest:
                        best_dest = cand + egr[b_stop]
        # walk to neighbouring stops, from where a bus set the rider down (walks are not chained)
        n_bus = n_next
        for i in range(n_bus):
            bus_time[i] = tau[k, nxt[i]]
        for i in range(n_bus):
            b_stop = nxt[i]
            for q in range(fp_off[b_stop], fp_off[b_stop + 1]):
                c = fp_to[q]
                t = bus_time[i] + fp_s[q]
                if t < best[c] and t < best_dest:
                    tau[k, c] = t
                    best[c] = t
                    par_walk[k, c] = b_stop
                    if not is_marked[c]:
                        is_marked[c] = True
                        nxt[n_next] = c
                        n_next += 1
                    if t + egr[c] < best_dest:
                        best_dest = t + egr[c]
        dest_time[k] = INF
        dest_stop[k] = -1
        for i in range(len(egr_stop)):
            s = egr_stop[i]
            if tau[k, s] + egr_s[i] < dest_time[k]:
                dest_time[k] = tau[k, s] + egr_s[i]
                dest_stop[k] = s
        n_marked = n_next
        for i in range(n_next):
            marked[i] = nxt[i]
        if n_marked == 0:
            break
    return 0


@njit(cache=True)
def ride_seconds(run_est, dwell_est, ps_off, ps_stop, ps_seg, ps_run_m, p, a, b, board, dtp, lf, edges, t0):
    """Raw seconds on board from stop position a to b of pattern p, boarding at epoch 'board'.
    lf, edges, t0: the live multipliers and the moment they describe (see search)."""
    lo = ps_off[p]
    ride = 0.0
    for j in range(a, b):
        row = lo + j
        if ps_seg[row] >= 0 and ps_run_m[row] > 0.0:
            h = 0
            while h < len(edges) and board + ride - t0 >= edges[h]:
                h += 1
            ride += _lookup(run_est, ps_seg[row], dtp, board + ride) * ps_run_m[row] * lf[h, ps_seg[row]]
        if j + 1 < b:
            ride += _lookup(dwell_est, ps_stop[row + 1], dtp, board + ride)
    return ride
