import numpy as np
import math
import matplotlib

matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from qgis.core import QgsGeometry, QgsPointXY


# =========================================================
# 1. EKSTRAK PROFIL DARI DEM
# =========================================================
def extract_profile(dem_layer, line_feature, interval):

    geom = line_feature.geometry()
    if geom.isMultipart():
        geom = QgsGeometry.fromPolylineXY(geom.asMultiPolyline()[0])

    provider = dem_layer.dataProvider()
    length = geom.length()

    xs, zs = [], []
    d = 0.0
    while d <= length + 1e-9:
        pt = geom.interpolate(d).asPoint()
        z, ok = provider.sample(QgsPointXY(pt), 1)
        if ok:
            xs.append(d)
            zs.append(z)
        d += interval

    if not xs:
        raise ValueError(
            "No profile points found from the DEM.\n"
            "Make sure the cross-section line lies over the DEM area."
        )
    return np.array(xs), np.array(zs)


# =========================================================
# 2. SAMPLING VARIABEL ACAK (Monte Carlo)
# =========================================================
def sample_property(mean, dist, std=0.0, rel_min=None, rel_max=None, max_tries=1000):

    has_bounds = (
        rel_min is not None and rel_max is not None and rel_max > rel_min
    )

    # ── UNIFORM ──────────────────────────────────────────────────
    if dist == "Uniform":
        if has_bounds:
            return float(np.random.uniform(rel_min, rel_max))
        # Belum ada Rel. Min/Max eksplisit → fallback longgar di sekitar mean
        lo, hi = (mean, mean) if mean == 0 else sorted([mean * 0.5, mean * 1.5])
        if lo == hi:
            return mean
        return float(np.random.uniform(lo, hi))

    # ── LOGNORMAL ────────────────────────────────────────────────
    if dist == "Lognormal":
        if mean <= 0 or std <= 0:
            return mean  # lognormal butuh mean & std > 0, fallback aman
        cov = std / mean
        sigma_ln = math.sqrt(math.log(1.0 + cov ** 2))
        mu_ln = math.log(mean) - 0.5 * sigma_ln ** 2

        if not has_bounds:
            return float(np.random.lognormal(mu_ln, sigma_ln))

        for _ in range(max_tries):
            val = float(np.random.lognormal(mu_ln, sigma_ln))
            if rel_min <= val <= rel_max:
                return val
        return mean

    # ── NORMAL (default) ────────────────────────────────────────
    if std <= 0:
        return mean

    if not has_bounds:
        return float(np.random.normal(mean, std))

    for _ in range(max_tries):
        val = float(np.random.normal(mean, std))
        if rel_min <= val <= rel_max:
            return val
    return mean

# =========================================================
# 3. BANGUN BIDANG GELINCIR LINGKARAN
# =========================================================
def _find_circle_ground_intersections(xc, zc, R, xs, zs, tol=0.50):

    intersections = []

    # Scan sepanjang profil untuk menemukan intersection
    for i in range(len(xs) - 1):
        x1, z1 = xs[i], zs[i]
        x2, z2 = xs[i + 1], zs[i + 1]

        # Circle equation: (x - xc)² + (z - zc)² = R²
        for alpha in np.linspace(0, 1, 30):
            x = x1 + alpha * (x2 - x1)
            z = z1 + alpha * (z2 - z1)

            dist = math.sqrt((x - xc) ** 2 + (z - zc) ** 2)

            if abs(dist - R) < tol:
                if z < zc:
                    intersections.append((float(x), float(z)))

    if not intersections:
        return None

    unique = []
    for point in intersections:
        is_duplicate = False
        for existing in unique:
            if abs(point[0] - existing[0]) < tol and abs(point[1] - existing[1]) < tol:
                is_duplicate = True
                break
        if not is_duplicate:
            unique.append(point)

    unique.sort(key=lambda p: p[0])

    if len(unique) >= 2:
        return [unique[0], unique[-1]]

    return None


def build_slip_surface(xc, zc, R, xs, zs):

    # Step 1: Cari intersection points (entry & exit)
    intersections = _find_circle_ground_intersections(xc, zc, R, xs, zs)

    if intersections is None or len(intersections) < 2:
        return None

    entry_x, entry_z = intersections[0]
    exit_x, exit_z = intersections[-1]

    # Step 2: Verifikasi entry dan exit berada dalam batas profil
    x_min, x_max = float(xs.min()), float(xs.max())
    if entry_x < x_min or exit_x > x_max:
        return None

    if entry_x >= exit_x:  # Entry harus di kiri exit
        return None

    # Step 3: Bangun busur antara entry dan exit
    # Gunakan dense sampling untuk akurasi tinggi
    span = exit_x - entry_x
    n_points = max(70, int(span / 0.3))  # Lebih padat: 70 points minimum, 0.3m interval
    arc_x = []
    arc_z = []

    for x in np.linspace(entry_x, exit_x, n_points):
        dx = x - xc
        discriminant = R ** 2 - dx ** 2

        if discriminant < 0:
            continue

        # Ambil busur bawah saja (z < zc)
        z_arc = zc - math.sqrt(discriminant)

        # Verifikasi titik berada di bawah ground surface
        z_ground = np.interp(x, xs, zs)

        # Margin 0.25m untuk DEM noise dan floating point errors
        if z_arc < z_ground + 0.25:
            arc_x.append(float(x))
            arc_z.append(float(z_arc))

    if len(arc_x) < 5:
        return None

    return np.array(arc_x), np.array(arc_z)


def _ground_z(x, xs, zs):
    return float(np.interp(x, xs, zs))


def validate_surface(arc_x, arc_z, xs, zs, min_span_ratio=0.05):

    if len(arc_x) < 5:
        return False

    x_min, x_max = float(xs.min()), float(xs.max())
    z_min, z_max = float(zs.min()), float(zs.max())
    z_bottom = z_min - 2.0 * (z_max - z_min)

    entry_x, entry_z = float(arc_x[0]), float(arc_z[0])
    exit_x, exit_z = float(arc_x[-1]), float(arc_z[-1])

    # ① Entry dan exit harus MENYENTUH ground surface
    z_ground_entry = float(np.interp(entry_x, xs, zs))
    z_ground_exit = float(np.interp(exit_x, xs, zs))

    entry_gap = abs(entry_z - z_ground_entry)
    exit_gap = abs(exit_z - z_ground_exit)

    tolerance = 2.0  # meter
    if entry_gap > tolerance:
        return False
    if exit_gap > tolerance:
        return False

    # ② Mayoritas titik arc harus di bawah ground surface
    n_above = 0
    for x, z in zip(arc_x, arc_z):
        z_ground = float(np.interp(x, xs, zs))

        # Skip entry/exit (sudah divalidasi di atas)
        if abs(x - entry_x) < 0.01 or abs(x - exit_x) < 0.01:
            continue

        # Untuk interior points: allow small penetration (0.5m)
        # karena DEM interpolation tidak sempurna
        if z > z_ground + 0.5:
            n_above += 1

    if n_above > len(arc_x) * 0.2:
        return False

    # ③ Span horizontal minimum
    span = exit_x - entry_x
    total = x_max - x_min
    if span < min_span_ratio * total:
        return False

    # ④ Entry & exit dalam batas profil
    if entry_x < x_min or exit_x > x_max:
        return False

    # ⑤ Tidak menembus batas bawah boundary
    if float(np.min(arc_z)) < z_bottom:
        return False

    # ⑥ Kedalaman slip surface harus signifikan
    H = z_max - z_min
    min_depth = 0.03 * H

    # Cari kedalaman maksimum slip surface dari ground
    max_depth = 0.0
    for x, z in zip(arc_x, arc_z):
        z_ground = float(np.interp(x, xs, zs))
        depth = z_ground - z
        if depth > max_depth:
            max_depth = depth

    if max_depth < min_depth:
        return False

    return True


def validate_surface_relaxed(arc_x, arc_z, xs, zs, min_span_ratio=0.03):

    if len(arc_x) < 5:
        return False

    x_min, x_max = float(xs.min()), float(xs.max())
    z_min, z_max = float(zs.min()), float(zs.max())
    z_bottom = z_min - 2.0 * (z_max - z_min)

    entry_x, entry_z = float(arc_x[0]), float(arc_z[0])
    exit_x, exit_z = float(arc_x[-1]), float(arc_z[-1])

    # ① Entry/exit tolerance
    z_ground_entry = float(np.interp(entry_x, xs, zs))
    z_ground_exit = float(np.interp(exit_x, xs, zs))

    tolerance = 3.0
    if abs(entry_z - z_ground_entry) > tolerance:
        return False
    if abs(exit_z - z_ground_exit) > tolerance:
        return False

    # ② Interior points
    n_above = 0
    for x, z in zip(arc_x, arc_z):
        z_ground = float(np.interp(x, xs, zs))

        if abs(x - entry_x) < 0.01 or abs(x - exit_x) < 0.01:
            continue

        if z > z_ground + 0.75:
            n_above += 1

    if n_above > len(arc_x) * 0.3:
        return False

    # ③ Min span
    span = exit_x - entry_x
    total = x_max - x_min
    if span < min_span_ratio * total:
        return False

    # ④ Bounds check
    if entry_x < x_min or exit_x > x_max:
        return False

    if float(np.min(arc_z)) < z_bottom:
        return False

    # ⑤ Min depth
    H = z_max - z_min
    min_depth = 0.02 * H  # RELAXED

    max_depth = 0.0
    for x, z in zip(arc_x, arc_z):
        z_ground = float(np.interp(x, xs, zs))
        depth = z_ground - z
        if depth > max_depth:
            max_depth = depth

    if max_depth < min_depth:
        return False

    return True


# =========================================================
# 4. METODE BISHOP DISEDERHANAKAN
# =========================================================
def bishop_FS(arc_x, arc_z, xs, zs,
              unit_weight, cohesion, phi_rad,
              n_slices=25, max_iter=100, tol=1e-6):

    x_start, x_end = float(arc_x[0]), float(arc_x[-1])
    if x_end <= x_start:
        return None

    xi = np.linspace(x_start, x_end, n_slices + 1)
    zi_slip = np.interp(xi, arc_x, arc_z)
    zi_gnd = np.interp(xi, xs, zs)
    tan_phi = math.tan(phi_rad)

    # Deteksi arah longsoran dari profil lereng
    # Profil turun ke kanan (normal): sign = -1  → alpha = atan2(-(dz), b)  [kode asli]
    # Profil naik ke kanan (terbalik): sign = +1 → alpha = atan2(+(dz), b)  [fix]
    z_entry = float(np.interp(float(arc_x[0]), xs, zs))
    z_exit  = float(np.interp(float(arc_x[-1]), xs, zs))
    _bishop_sign = -1.0 if z_entry > z_exit else 1.0

    FS = 1.0

    for _ in range(max_iter):
        num = den = 0.0
        for i in range(n_slices):
            b = float(xi[i + 1] - xi[i])
            if b <= 0.0:
                continue
            h = 0.5 * (max(zi_gnd[i] - zi_slip[i], 0.0) +
                       max(zi_gnd[i + 1] - zi_slip[i + 1], 0.0))
            if h <= 0.0:
                continue
            W = unit_weight * h * b
            # FIX: Deteksi arah longsoran dari profil, sesuaikan tanda alpha
            alpha = math.atan2(_bishop_sign * (zi_slip[i + 1] - zi_slip[i]), b)
            sin_a = math.sin(alpha)
            cos_a = math.cos(alpha)
            m_alpha = cos_a + sin_a * tan_phi / FS
            if abs(m_alpha) < 1e-9:
                continue
            num += (cohesion * b + W * tan_phi) / m_alpha
            den += W * sin_a

        if den <= 0.0:
            return None

        FS_new = num / den

        if FS_new <= 0.0:
            return None

        if abs(FS_new - FS) < tol:
            return float(FS_new)
        FS = FS_new

    return float(FS) if FS > 0.0 else None


# =========================================================
# 5. BANGUN BOUNDARY
# =========================================================
def build_boundary(xs, zs):

    x_min, x_max = float(xs.min()), float(xs.max())
    z_min, z_max = float(zs.min()), float(zs.max())
    dz = z_max - z_min
    x_ext = x_max - x_min
    z_bottom = z_min - 2.0 * dz

    # Titik-titik boundary mengikuti profil tanah di bagian atas
    bnd_x = (
            [x_min - x_ext]  # pojok kiri atas
            + list(xs)  # profil tanah
            + [x_max + x_ext,  # pojok kanan atas
               x_max + x_ext,  # pojok kanan bawah
               x_min - x_ext,  # pojok kiri bawah
               x_min - x_ext]  # tutup (= titik pertama)
    )
    bnd_z = (
            [float(zs[0])]  # elevasi ujung kiri profil
            + list(zs)  # profil tanah
            + [float(zs[-1]),  # elevasi ujung kanan profil
               z_bottom,  # bawah kanan
               z_bottom,  # bawah kiri
               float(zs[0])]  # tutup
    )

    return bnd_x, bnd_z


def build_boundary_qgs(xs, zs):

    x_min, x_max = float(xs.min()), float(xs.max())
    z_min, z_max = float(zs.min()), float(zs.max())
    dz = z_max - z_min
    x_ext = x_max - x_min
    z_bottom = z_min - 2.0 * dz

    pts = (
            [QgsPointXY(x_min - x_ext, float(zs[0]))]
            + [QgsPointXY(float(x), float(z)) for x, z in zip(xs, zs)]
            + [
                QgsPointXY(x_max + x_ext, float(zs[-1])),
                QgsPointXY(x_max + x_ext, z_bottom),
                QgsPointXY(x_min - x_ext, z_bottom),
                QgsPointXY(x_min - x_ext, float(zs[0])),
            ]
    )
    return pts


# =========================================================
# 6. GRID SEARCH DETERMINISTIK
# =========================================================
def grid_search_critical_surface(xs, zs,
                                 unit_weight, cohesion, phi_rad,
                                 n_cx=20, n_cy=20, n_r=10,
                                 progress_callback=None,
                                 track_all_attempts=False,
                                 use_relaxed_validation=False,
                                 slide_compatible=False):

    x_min, x_max = float(xs.min()), float(xs.max())
    z_min, z_max = float(zs.min()), float(zs.max())
    H = z_max - z_min
    dx = x_max - x_min

    # ── STEP 1: Define 2D Grid untuk Centers ──
    # Grid di dalam dan sekitar slope domain
    cx_vals = np.linspace(x_min - 0.3 * dx, x_max + 0.5 * dx, n_cx)
    cy_vals = np.linspace(z_max + 0.2 * H, z_max + 1.5 * H, n_cy)

    best_FS = float('inf')
    best_arc = None
    best_center = None
    all_arcs = []  # Valid arcs only (backward compatibility)
    all_attempts = []  # ALL attempts including invalid

    total_centers = n_cx * n_cy
    current_center = 0

    # Pilih validation function
    validate_fn = validate_surface_relaxed if use_relaxed_validation else validate_surface

    # ── STEP 2: Untuk setiap center, calculate valid R range ──
    for xc in cx_vals:
        for zc in cy_vals:
            current_center += 1

            # Calculate R range berdasarkan geometry
            # R_min: harus bisa reach slope toe
            # R_max: tidak terlalu besar (deep seated limit)
            R_min = max(0.3 * H, zc - z_max + 0.5 * H)
            R_max = min(2.0 * (H + dx), zc - z_min + H)

            if R_max <= R_min:
                # Track invalid geometry jika diminta
                if track_all_attempts:
                    R_mid = (R_min + R_max) / 2 if R_max > 0 else 50.0
                    all_attempts.append({
                        'xc': float(xc),
                        'zc': float(zc),
                        'R': float(R_mid),
                        'FS': -1000.0,
                        'status': 'INVALID_GEOMETRY'
                    })
                continue

            # Test multiple R values untuk center ini
            r_vals = np.linspace(R_min, R_max, n_r)

            for i, R in enumerate(r_vals):
                # Progress callback
                progress = current_center * n_r + i
                total = total_centers * n_r
                if progress_callback is not None:
                    progress_callback(progress, total)

                # Build slip surface
                arc = build_slip_surface(xc, zc, R, xs, zs)
                if arc is None:
                    if track_all_attempts:
                        all_attempts.append({
                            'xc': float(xc),
                            'zc': float(zc),
                            'R': float(R),
                            'FS': -1000.0,
                            'status': 'INVALID_ARC'
                        })
                    continue
                arc_x, arc_z = arc

                # Validate
                if not validate_fn(arc_x, arc_z, xs, zs):
                    if track_all_attempts:
                        all_attempts.append({
                            'xc': float(xc),
                            'zc': float(zc),
                            'R': float(R),
                            'FS': -1000.0,
                            'status': 'INVALID_VALIDATION'
                        })
                    continue

                # Calculate FS
                FS = bishop_FS(arc_x, arc_z, xs, zs,
                               unit_weight, cohesion, phi_rad)
                if FS is None or FS <= 0.0:
                    if track_all_attempts:
                        all_attempts.append({
                            'xc': float(xc),
                            'zc': float(zc),
                            'R': float(R),
                            'FS': -1000.0,
                            'status': 'INVALID_BISHOP'
                        })
                    continue

                # Filter extreme values
                if FS > 10.0:
                    if track_all_attempts:
                        all_attempts.append({
                            'xc': float(xc),
                            'zc': float(zc),
                            'R': float(R),
                            'FS': -1000.0,
                            'status': 'EXTREME_FS'
                        })
                    continue

                # ✅ VALID candidate
                arc_dict = {
                    'arc_x': arc_x.copy(),
                    'arc_z': arc_z.copy(),
                    'xc': float(xc),
                    'zc': float(zc),
                    'R': float(R),
                    'FS': FS,
                }
                all_arcs.append(arc_dict)

                if track_all_attempts:
                    attempt_dict = arc_dict.copy()
                    attempt_dict['status'] = 'VALID'
                    all_attempts.append(attempt_dict)

                if FS < best_FS:
                    best_FS = FS
                    best_arc = (arc_x.copy(), arc_z.copy())
                    best_center = (float(xc), float(zc), float(R))

    if best_arc is None:
        error_msg = (
            "No valid slip surface found.\n"
            f"Centers tested: {total_centers}\n"
        )
        if track_all_attempts:
            valid_count = len(all_arcs)
            invalid_count = len(all_attempts) - valid_count
            success_rate = valid_count / len(all_attempts) * 100 if all_attempts else 0
            error_msg += (
                f"Valid: {valid_count}, Invalid: {invalid_count}\n"
                f"Success rate: {success_rate:.1f}%\n"
            )
        error_msg += "Check: DEM quality, cross-section length, soil parameters"
        raise ValueError(error_msg)

    # Return format sesuai mode
    if slide_compatible and track_all_attempts:
        valid_attempts = [a for a in all_attempts if a.get('status') == 'VALID']
        invalid_attempts = [a for a in all_attempts if a.get('status') != 'VALID']

        statistics = {
            'total_attempts': len(all_attempts),
            'valid_count': len(valid_attempts),
            'invalid_count': len(invalid_attempts),
            'success_rate': len(valid_attempts) / len(all_attempts) * 100 if all_attempts else 0,
            'best_FS': best_FS,

            # Breakdown by failure reason
            'invalid_geometry': len([a for a in all_attempts if a.get('status') == 'INVALID_GEOMETRY']),
            'invalid_arc': len([a for a in all_attempts if a.get('status') == 'INVALID_ARC']),
            'invalid_validation': len([a for a in all_attempts if a.get('status') == 'INVALID_VALIDATION']),
            'invalid_bishop': len([a for a in all_attempts if a.get('status') == 'INVALID_BISHOP']),
            'extreme_fs': len([a for a in all_attempts if a.get('status') == 'EXTREME_FS']),
        }

        return {
            'best_FS': best_FS,
            'best_arc': best_arc,
            'best_center': best_center,
            'all_attempts': all_attempts,
            'valid_attempts': valid_attempts,
            'statistics': statistics
        }
    else:
        # Original format (backward compatible)
        return best_FS, best_arc, best_center, all_arcs

# =========================================================
# 7. MONTE CARLO PROBABILISTIK
# =========================================================
def calculate_reliability_stats(FS_arr):

    FS_mean = float(np.mean(FS_arr))
    FS_std = float(np.std(FS_arr))

    # Reliability Index β = (μ_FS - 1) / σ_FS
    # β > 3: Very reliable, β < 1: Unreliable
    beta = (FS_mean - 1.0) / FS_std if FS_std > 0 else float('inf')

    # Coefficient of Variation
    cov = FS_std / FS_mean if FS_mean > 0 else 0.0

    # Percentiles
    p5 = float(np.percentile(FS_arr, 5))
    p25 = float(np.percentile(FS_arr, 25))
    p50 = float(np.percentile(FS_arr, 50))  # Median
    p75 = float(np.percentile(FS_arr, 75))
    p95 = float(np.percentile(FS_arr, 95))

    return {
        'beta': beta,
        'cov': cov,
        'p5': p5,
        'p25': p25,
        'p50': p50,
        'p75': p75,
        'p95': p95,
    }


def interpret_fs(fs):

    if fs < 1.0:
        return "UNSTABLE", "Slope is in a failure/critical condition", "#D32F2F"
    elif fs < 1.1:
        return "CRITICAL", "Slope is very unsafe", "#E64A19"
    elif fs < 1.25:
        return "LOW", "Slope has low safety, reinforcement needed", "#F57C00"
    elif fs < 1.5:
        return "MODERATE", "Slope is moderately safe under normal conditions", "#FBC02D"
    elif fs < 2.0:
        return "SAFE", "Slope is safe under normal conditions", "#388E3C"
    else:
        return "VERY SAFE", "Slope is very safe", "#1976D2"


def interpret_probability_failure(pf):

    if pf > 0.8:
        return "VERY HIGH", "Very high risk of failure", "#D32F2F"
    elif pf > 0.5:
        return "HIGH", "High risk of failure", "#F57C00"
    elif pf > 0.3:
        return "MODERATE", "Moderate risk of failure", "#FBC02D"
    elif pf > 0.05:
        return "LOW", "Low risk of failure", "#388E3C"
    else:
        return "VERY LOW", "Very low risk of failure", "#1976D2"


def interpret_reliability_index(beta):

    if beta < 0:
        return "UNACCEPTABLE", "β < 0: Dangerous condition"
    elif beta < 1.0:
        return "VERY LOW", "β < 1: Very low reliability"
    elif beta < 2.0:
        return "LOW", "1 < β < 2: Low reliability"
    elif beta < 3.0:
        return "MODERATE", "2 < β < 3: Moderate reliability"
    elif beta < 4.0:
        return "HIGH", "3 < β < 4: High reliability"
    else:
        return "VERY HIGH", "β > 4: Very high reliability"


def calculate_convergence_data(FS_arr, window=50):

    n = len(FS_arr)
    iterations = []
    means = []
    stds = []

    for i in range(window, n + 1, window):
        subset = FS_arr[:i]
        iterations.append(i)
        means.append(np.mean(subset))
        stds.append(np.std(subset))

    # Check convergence: std perubahan < 1%
    if len(means) > 2:
        recent_change = abs(means[-1] - means[-2]) / means[-2] if means[-2] != 0 else 0
        converged = recent_change < 0.01
    else:
        converged = False

    return {
        'iterations': iterations,
        'mean': means,
        'std': stds,
        'converged': converged
    }


def monte_carlo_analysis(xs, zs, critical_arc,
                         material_stats,
                         n_mc=1000, progress_callback=None):

    arc_x, arc_z = critical_arc
    FS_list = []

    g = material_stats["gamma"]
    c = material_stats["cohesion"]
    p = material_stats["phi"]

    for i in range(n_mc):
        if progress_callback is not None:
            progress_callback(i + 1, n_mc)

        gamma = sample_property(g["mean"], g["dist"], g.get("std", 0.0),
                                g.get("rel_min"), g.get("rel_max"))
        cohesion = sample_property(c["mean"], c["dist"], c.get("std", 0.0),
                                   c.get("rel_min"), c.get("rel_max"))
        phi_d = sample_property(p["mean"], p["dist"], p.get("std", 0.0),
                                p.get("rel_min"), p.get("rel_max"))
        phi_r = math.radians(phi_d)

        # Catatan: cohesion boleh 0 (tanah non-kohesif), makanya >= bukan >
        if gamma <= 0.0 or cohesion < 0.0 or phi_r <= 0.0:
            continue

        FS = bishop_FS(arc_x, arc_z, xs, zs, gamma, cohesion, phi_r)
        if FS is not None and FS > 0.0:
            FS_list.append(FS)

    if len(FS_list) < 10:
        raise ValueError(
            f"Only {len(FS_list)} valid Monte Carlo iterations. "
            "Check the Mean/Std. Dev/Rel. Min/Rel. Max values in Material Statistics."
        )

    FS_arr = np.array(FS_list)

    # Statistik dasar
    FS_mean = float(np.mean(FS_arr))
    FS_std = float(np.std(FS_arr))
    Pf = float(np.sum(FS_arr < 1.0) / len(FS_arr))

    # Statistik lanjutan (dari kode user)
    reliability_stats = calculate_reliability_stats(FS_arr)

    # Convergence data
    convergence = calculate_convergence_data(FS_arr, window=max(50, n_mc // 20))

    return (FS_mean, FS_std, Pf, FS_arr, reliability_stats, convergence)


# =========================================================
# 8. HELPER CANVAS
# =========================================================
def build_q_cone(xc, zc, arc_x, arc_z):
    return [(QgsPointXY(xc, zc), QgsPointXY(float(x), float(z)))
            for x, z in zip(arc_x, arc_z)]


# =========================================================
# 9. PLOT HASIL
# =========================================================
def plot_results(result, material_stats=None):

    if material_stats is None:
        material_stats = result.get("material_stats", {})

    xs = result["xs"]
    zs = result["zs"]
    arc_x, arc_z = result["best_arc"]
    xc, zc, R = result["best_center"]
    FS_det = result["FS_det"]
    FS_mean = result["FS_mean"]
    FS_std = result["FS_std"]
    Pf = result["Pf"]
    all_arcs = result["all_arcs"]

    # Statistik lanjutan (HYBRID)
    reliability_stats = result.get("reliability_stats", {})
    convergence = result.get("convergence", {})
    fs_interp = result.get("fs_interpretation", {})
    pf_interp = result.get("pf_interpretation", {})
    beta_interp = result.get("beta_interpretation", {})

    # ── Entry / Exit busur kritis ─────────────────────────────────
    entry_x, entry_z = float(arc_x[0]), float(arc_z[0])
    exit_x, exit_z = float(arc_x[-1]), float(arc_z[-1])

    # ── Figure dengan 2 subplots ──────────────────────────────────
    fig = plt.figure(figsize=(16, 8))

    # Main plot (kiri) - Slip Surface
    ax1 = plt.subplot(1, 2, 1)
    fig.patch.set_facecolor('white')
    ax1.set_facecolor('white')

    # 2. Titik Q hijau seragam
    if all_arcs:
        qx = [a['xc'] for a in all_arcs]
        qz = [a['zc'] for a in all_arcs]
        ax1.scatter(qx, qz,
                    c='#4CAF50', s=18, alpha=0.55,
                    linewidths=0, label='Q (Grid Search)', zorder=3)

    # 3. Q minimum FS dengan warna interpretasi
    fs_color = fs_interp.get('color', 'red')
    ax1.scatter([xc], [zc], marker='x', c=fs_color, s=150,
                linewidths=2.5, label='Q minimum FS', zorder=6)
    ax1.annotate(f' {FS_det:.3f}',
                 xy=(xc, zc),
                 xytext=(xc + (xs.max() - xs.min()) * 0.03,
                         zc + (zs.max() - zs.min()) * 0.08),
                 fontsize=9, fontweight='bold', color=fs_color,
                 bbox=dict(boxstyle='round,pad=0.25',
                           fc='white', ec='#cccccc', alpha=0.9),
                 zorder=7)

    # 4. Radius Q→Entry dan Q→Exit
    ax1.plot([xc, entry_x], [zc, entry_z],
             'k--', linewidth=1.0, label='Radius Q-Entry', zorder=5)
    ax1.plot([xc, exit_x], [zc, exit_z],
             'k--', linewidth=1.0, label='Radius Q-Exit', zorder=5)

    # 5. Irisan tanah hatch oranye
    gnd_on_arc = np.interp(arc_x, xs, zs)
    ax1.fill_between(arc_x, arc_z, gnd_on_arc,
                     facecolor='#F5DEB3', edgecolor='#8B6914',
                     hatch='|||||', linewidth=0.3,
                     alpha=0.85, zorder=4)

    # 6. Busur kritis (biru tebal)
    ax1.plot(arc_x, arc_z,
             '-', color='#1565C0', linewidth=2.5,
             label='Slip Surface', zorder=8)

    # 7. Permukaan tanah (hitam tebal)
    ax1.plot(xs, zs, 'k-', linewidth=2.2, label='Ground Surface', zorder=9)

    # 8. Entry / Exit (titik biru)
    ax1.scatter([entry_x, exit_x], [entry_z, exit_z],
                c='#1565C0', s=60, zorder=10, label='Entry / Exit')

    # ── Axis labels, title ─────────────────────────────────────
    ax1.set_xlabel('Distance (m)', fontsize=11)
    ax1.set_ylabel('Elevation (m)', fontsize=11)

    # Title with color interpretation
    title_text = (
        f'Bishop Monte Carlo  |  FSmin = {FS_det:.3f} ({fs_interp.get("category", "")})\n'
        f'FS̄ = {FS_mean:.3f} ± {FS_std:.3f}  |  '
        f'P(failure) = {Pf * 100:.2f}% ({pf_interp.get("category", "")})'
    )
    ax1.set_title(title_text, fontsize=10, fontweight='bold')

    ax1.grid(True, linestyle='--', linewidth=0.35, alpha=0.5, color='gray')
    ax1.set_axisbelow(True)
    ax1.legend(loc='upper right', fontsize=8, framealpha=0.92)

    # ── Right panel: Statistics & Convergence ──────────────────
    ax2 = plt.subplot(1, 2, 2)
    ax2.axis('off')

    # Statistik lengkap
    beta = reliability_stats.get('beta', 0)
    g_stats = material_stats.get('gamma', {})
    c_stats = material_stats.get('cohesion', {})
    p_stats = material_stats.get('phi', {})

    stats_text = f"""
SLOPE STABILITY ANALYSIS RESULTS
{'=' * 50}

DETERMINISTIC (Grid Search):
  Min. FoS      : {FS_det:.3f}
  Status        : {fs_interp.get('category', 'N/A')}
  Description   : {fs_interp.get('description', '')}

PROBABILISTIC (Monte Carlo):
  Mean FoS         : {FS_mean:.3f}
  Std Dev          : {FS_std:.3f}
  P(failure)       : {Pf * 100:.2f}%
  Risk             : {pf_interp.get('category', 'N/A')}
  Reliability Index (β) : {beta:.2f}
  β Interpretation : {beta_interp.get('category', 'N/A')}

CRITICAL SLIP SURFACE:
  Center (xc, zc) : ({xc:.2f}m, {zc:.2f}m)
  Radius (R)      : {R:.2f}m

SOIL PARAMETERS:
  γ  = {g_stats.get('mean', 0)} kN/m³  ({g_stats.get('dist', 'Normal')})
  c' = {c_stats.get('mean', 0)} kPa  ({c_stats.get('dist', 'Normal')})
  φ' = {p_stats.get('mean', 0)}°  ({p_stats.get('dist', 'Normal')})
"""

    ax2.text(0.05, 0.95, stats_text, transform=ax2.transAxes,
             fontsize=9, verticalalignment='top', family='monospace',
             bbox=dict(boxstyle='round', facecolor='#F5F5F5', alpha=0.8))

    plt.tight_layout()
    return fig


# =========================================================
# 10. MAIN PIPELINE
# =========================================================
def run_full_analysis(
        dem_layer,
        line_feature,
        interval,
        material_stats,
        n_mc=1000,
        n_grid_cx=20,
        n_grid_cy=15,
        n_grid_r=10,
        progress_callback=None,
):

    if progress_callback:
        progress_callback('profile', 0, 1, 'Extracting DEM profile...')
    xs, zs = extract_profile(dem_layer, line_feature, interval)
    if len(xs) < 5:
        raise ValueError("Profile is too short.")
    if progress_callback:
        progress_callback('profile', 1, 1, 'Profile extraction complete.')

    # Grid search deterministik pakai nilai MEAN tiap properti
    gamma_mean = material_stats["gamma"]["mean"]
    c_mean = material_stats["cohesion"]["mean"]
    phi_mean = material_stats["phi"]["mean"]
    phi_mean_rad = math.radians(phi_mean)

    def _gcb(cur, tot):
        if progress_callback:
            progress_callback('grid', cur, tot,
                              f'Grid search: {cur}/{tot}...')

    FS_det, best_arc, best_center, all_arcs = grid_search_critical_surface(
        xs, zs,
        unit_weight=gamma_mean, cohesion=c_mean, phi_rad=phi_mean_rad,
        n_cx=n_grid_cx, n_cy=n_grid_cy, n_r=n_grid_r,
        progress_callback=_gcb,
    )

    def _mcb(cur, tot):
        if progress_callback:
            progress_callback('mc', cur, tot,
                              f'Monte Carlo: iteration {cur}/{tot}...')

    FS_mean, FS_std, Pf, FS_arr, reliability_stats, convergence = monte_carlo_analysis(
        xs, zs, best_arc,
        material_stats=material_stats,
        n_mc=n_mc, progress_callback=_mcb,
    )

    xc, zc, R = best_center
    arc_x, arc_z = best_arc

    # Interpretasi hasil
    fs_category, fs_desc, fs_color = interpret_fs(FS_det)
    pf_category, pf_desc, pf_color = interpret_probability_failure(Pf)
    beta_category, beta_desc = interpret_reliability_index(reliability_stats['beta'])

    return {
        "FS_det": FS_det,
        "FS_mean": FS_mean,
        "FS_std": FS_std,
        "Pf": Pf,
        "best_arc": best_arc,
        "best_center": best_center,
        "cone_lines": build_q_cone(xc, zc, arc_x, arc_z),
        "xs": xs,
        "zs": zs,
        "all_arcs": all_arcs,
        # Advanced statistics (HYBRID)
        "reliability_stats": reliability_stats,
        "convergence": convergence,
        "FS_array": FS_arr,
        # Simpan juga material_stats yang dipakai, supaya plot_results()
        # bisa menampilkan Mean & Distribution tiap properti tanpa perlu
        # dikirim ulang terpisah oleh pemanggil.
        "material_stats": material_stats,
        # Interpretations
        "fs_interpretation": {
            "category": fs_category,
            "description": fs_desc,
            "color": fs_color
        },
        "pf_interpretation": {
            "category": pf_category,
            "description": pf_desc,
            "color": pf_color
        },
        "beta_interpretation": {
            "category": beta_category,
            "description": beta_desc
        },
    }


# =========================================================
# 11. EXPORT
# =========================================================
def export_to_slide_format(all_attempts, filepath):

    import csv

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t')

        # Header
        writer.writerow(['Center_x', 'Center_y', 'Radius', 'Factor_of_Safety'])

        # Data rows
        for attempt in all_attempts:
            writer.writerow([
                f"{attempt['xc']:.3f}",
                f"{attempt['zc']:.3f}",
                f"{attempt['R']:.3f}",
                f"{attempt['FS']:.5f}"
            ])

    return len(all_attempts)