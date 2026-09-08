# SLOPSIS-QGIS
SLOPSIS is a QGIS plugin developed to perform slope stability analysis directly within a GIS environment, using Bishop's Simplified Method as its core limit-equilibrium formulation. The plugin integrates terrain data, geotechnical parameters, and statistical analysis into a single automated workflow, consisting of two main stages:

1. Deterministic Grid Search — The plugin extracts a 2D ground surface profile along a user-defined cross-section line from a Digital Elevation Model (DEM), then systematically searches a grid of trial circular slip surface centers and radii. Each candidate surface is validated against geometric and physical criteria (entry/exit points on the ground surface, minimum span, minimum depth, and boundary constraints) before its Factor of Safety (FK) is computed via the iterative Bishop Simplified equation. The candidate with the lowest valid FK is selected as the critical slip surface.
2. Probabilistic Monte Carlo Analysis — Using the critical slip surface identified in the grid search, the plugin performs Monte Carlo simulation by resampling unit weight (γ), cohesion (c'), and friction angle (φ') from normal distributions defined by their mean and coefficient of variation (COV), consistent with RocScience Slide 6.0 conventions. This produces a distribution of FK values, from which the mean FK, standard deviation, reliability index (β), and probability of failure (Pf) are derived.

The plugin outputs an integrated visualization showing the critical slip surface, ground profile, and a statistical summary panel, along with automatic qualitative interpretation of slope stability status (based on FK), risk level (based on Pf), and reliability category (based on β).

# How To Install
1. Open the SLOPSIS repository page on GitHub, click the Code button, then select Download ZIP to download the plugin's source code.
<img width="1754" height="957" alt="Screenshot 2026-09-08 101844" src="https://github.com/user-attachments/assets/d30ece97-6338-404b-a03f-4bdded108940" />

2. The ZIP file (SLOPSIS---QGIS-main.zip) is successfully downloaded in the browser.
<img width="504" height="154" alt="Screenshot 2026-09-08 101858" src="https://github.com/user-attachments/assets/e7d0b740-e2ff-41f3-9c66-b5fef5d8c7d2" />

3. Open QGIS, then go to Plugins > Manage and Install Plugins > Install from ZIP. The ZIP file field is still empty at this stage.
<img width="920" height="1021" alt="Screenshot 2026-09-08 104226" src="https://github.com/user-attachments/assets/19a28222-d6d0-48a8-a2ed-28eb3d204f7f" />

4. Click the "…" button to browse for the downloaded ZIP file; its file path then appears in the ZIP file field.
<img width="1395" height="965" alt="Screenshot 2026-09-08 104248" src="https://github.com/user-attachments/assets/abc54355-3aa4-4b6e-9928-9e4cb13d4547" />
<img width="1395" height="965" alt="Screenshot 2026-09-08 104302" src="https://github.com/user-attachments/assets/3f4c31f3-81f5-45c2-884e-b351159feef2" />

5. Click Install Plugin — a green notification "Plugin installed successfully" confirms the installation was successful.
 <img width="1395" height="965" alt="Screenshot 2026-09-08 104317" src="https://github.com/user-attachments/assets/b73d0729-3d94-4f5f-9d76-7315d339fd7c" />

6. After installation, open the Plugins menu — SLOPSIS now appears in the list of available plugins in QGIS.
<img width="1920" height="1020" alt="Screenshot 2026-09-08 104941" src="https://github.com/user-attachments/assets/a74577e3-6646-4238-b65a-8cd0a5dcbe95" />

7. Click Plugins > SLOPSIS, then click the plugin's icon in the submenu to open the analysis window.
<img width="1025" height="708" alt="Screenshot 2026-09-08 110952" src="https://github.com/user-attachments/assets/3336814d-f377-4b4c-979c-2e07a4e39b6c" />

8. The SLOPSIS window opens. In the Cross Section 1 tab, select the DEM layer, the cross-section line, and the sampling interval, then fill in the soil statistical parameters (unit weight, friction angle, cohesion) along with the distribution type, standard deviation, and Rel. Min/Rel. Max range. Set the number of Monte Carlo iterations (e.g. 500).
<img width="1025" height="708" alt="Screenshot 2026-09-08 110952" src="https://github.com/user-attachments/assets/aac8f55c-30cc-4de5-8b0a-cdae1b5295b7" />

9. Optionally, add a Cross Section 2 tab (or more) with different soil parameters and a different cross-section line, using either a preset or a custom iteration count (e.g. 1,500x) — this lets the plugin analyse multiple slope cross-sections in a single run. Once all tabs are filled in, click Calculate to run the analysis.<img width="1025" height="708" alt="Screenshot 2026-09-08 105135" src="https://github.com/user-attachments/assets/2b7c65ea-28eb-43d5-89cc-185b38faa5da" />

10. The result
<img width="553" height="512" alt="Screenshot 2026-09-08 105420" src="https://github.com/user-attachments/assets/5bf8c446-49c6-4e09-8a1e-7c39b9348503" />
<img width="1440" height="864" alt="Screenshot 2026-09-08 105429" src="https://github.com/user-attachments/assets/8e1da2ff-1352-4657-86be-fee576e4532a" />
<img width="1440" height="864" alt="Screenshot 2026-09-08 105436" src="https://github.com/user-attachments/assets/498de13d-2c72-4df7-b81b-6ef813ac6915" />

