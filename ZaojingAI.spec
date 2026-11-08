# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\Administrator\\.qoderworkcn\\workspace\\mu8l7lfenzrqswc8\\outputs\\agent_service.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\Administrator\\.qoderworkcn\\workspace\\mu8l7lfenzrqswc8\\outputs\\webui', 'webui')],
    hiddenimports=['fontTools.ttLib.tables', 'fontTools.ttLib.tables.B_A_S_E_', 'fontTools.ttLib.tables.BitmapGlyphMetrics', 'fontTools.ttLib.tables.C_B_D_T_', 'fontTools.ttLib.tables.C_B_L_C_', 'fontTools.ttLib.tables.C_F_F_', 'fontTools.ttLib.tables.C_F_F__2', 'fontTools.ttLib.tables.C_O_L_R_', 'fontTools.ttLib.tables.C_P_A_L_', 'fontTools.ttLib.tables.D_S_I_G_', 'fontTools.ttLib.tables.D__e_b_g', 'fontTools.ttLib.tables.DefaultTable', 'fontTools.ttLib.tables.E_B_D_T_', 'fontTools.ttLib.tables.E_B_L_C_', 'fontTools.ttLib.tables.F_F_T_M_', 'fontTools.ttLib.tables.F__e_a_t', 'fontTools.ttLib.tables.G_D_E_F_', 'fontTools.ttLib.tables.G_P_O_S_', 'fontTools.ttLib.tables.G_S_U_B_', 'fontTools.ttLib.tables.G_V_A_R_', 'fontTools.ttLib.tables.G__l_a_t', 'fontTools.ttLib.tables.G__l_o_c', 'fontTools.ttLib.tables.H_V_A_R_', 'fontTools.ttLib.tables.I_F_T_', 'fontTools.ttLib.tables.I_F_T_X_', 'fontTools.ttLib.tables.J_S_T_F_', 'fontTools.ttLib.tables.L_T_S_H_', 'fontTools.ttLib.tables.M_A_T_H_', 'fontTools.ttLib.tables.M_V_A_R_', 'fontTools.ttLib.tables.O_S_2f_2', 'fontTools.ttLib.tables.S_T_A_T_', 'fontTools.ttLib.tables.S_V_G_', 'fontTools.ttLib.tables.S__i_l_f', 'fontTools.ttLib.tables.S__i_l_l', 'fontTools.ttLib.tables.T_S_I_B_', 'fontTools.ttLib.tables.T_S_I_C_', 'fontTools.ttLib.tables.T_S_I_D_', 'fontTools.ttLib.tables.T_S_I_J_', 'fontTools.ttLib.tables.T_S_I_P_', 'fontTools.ttLib.tables.T_S_I_S_', 'fontTools.ttLib.tables.T_S_I_V_', 'fontTools.ttLib.tables.T_S_I__0', 'fontTools.ttLib.tables.T_S_I__1', 'fontTools.ttLib.tables.T_S_I__2', 'fontTools.ttLib.tables.T_S_I__3', 'fontTools.ttLib.tables.T_S_I__5', 'fontTools.ttLib.tables.T_T_F_A_', 'fontTools.ttLib.tables.TupleVariation', 'fontTools.ttLib.tables.V_A_R_C_', 'fontTools.ttLib.tables.V_D_M_X_', 'fontTools.ttLib.tables.V_O_R_G_', 'fontTools.ttLib.tables.V_V_A_R_', 'fontTools.ttLib.tables._a_n_k_r', 'fontTools.ttLib.tables._a_v_a_r', 'fontTools.ttLib.tables._b_g_c_l', 'fontTools.ttLib.tables._b_s_l_n', 'fontTools.ttLib.tables._c_i_d_g', 'fontTools.ttLib.tables._c_m_a_p', 'fontTools.ttLib.tables._c_v_a_r', 'fontTools.ttLib.tables._c_v_t', 'fontTools.ttLib.tables._f_e_a_t', 'fontTools.ttLib.tables._f_p_g_m', 'fontTools.ttLib.tables._f_v_a_r', 'fontTools.ttLib.tables._g_a_s_p', 'fontTools.ttLib.tables._g_c_i_d', 'fontTools.ttLib.tables._g_l_y_f', 'fontTools.ttLib.tables._g_v_a_r', 'fontTools.ttLib.tables._h_d_m_x', 'fontTools.ttLib.tables._h_e_a_d', 'fontTools.ttLib.tables._h_h_e_a', 'fontTools.ttLib.tables._h_m_t_x', 'fontTools.ttLib.tables._k_e_r_n', 'fontTools.ttLib.tables._l_c_a_r', 'fontTools.ttLib.tables._l_o_c_a', 'fontTools.ttLib.tables._l_t_a_g', 'fontTools.ttLib.tables._m_a_x_p', 'fontTools.ttLib.tables._m_e_t_a', 'fontTools.ttLib.tables._m_o_r_t', 'fontTools.ttLib.tables._m_o_r_x', 'fontTools.ttLib.tables._n_a_m_e', 'fontTools.ttLib.tables._o_p_b_d', 'fontTools.ttLib.tables._p_o_s_t', 'fontTools.ttLib.tables._p_r_e_p', 'fontTools.ttLib.tables._p_r_o_p', 'fontTools.ttLib.tables._s_b_i_x', 'fontTools.ttLib.tables._t_r_a_k', 'fontTools.ttLib.tables._v_h_e_a', 'fontTools.ttLib.tables._v_m_t_x', 'fontTools.ttLib.tables.asciiTable', 'fontTools.ttLib.tables.grUtils', 'fontTools.ttLib.tables.otBase', 'fontTools.ttLib.tables.otConverters', 'fontTools.ttLib.tables.otData', 'fontTools.ttLib.tables.otDataSchema', 'fontTools.ttLib.tables.otTables', 'fontTools.ttLib.tables.otTraverse', 'fontTools.ttLib.tables.sbixGlyph', 'fontTools.ttLib.tables.sbixStrike', 'fontTools.ttLib.tables.ttProgram', 'fontTools.ttLib', 'fontTools.misc.arrayTools', 'fontTools.misc.textTools', 'fontTools.misc.psOperators', 'fontTools.misc.py2urllib'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['cryptography', 'PIL.AvifImagePlugin', 'PIL._avif'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
splash = Splash(
    'C:\\Users\\Administrator\\.qoderworkcn\\workspace\\mu8l7lfenzrqswc8\\outputs\\assets\\splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    text_size=12,
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='ZaojingAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
