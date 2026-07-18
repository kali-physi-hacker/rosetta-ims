export type InventoryItem = {
  sku_code: string
  name: string
  brand: string
  category: string
  supplier: string
  uom: string
  cost_price: number
  selling_price: number
  gp_floor: number         // decimal: 0.70 = 70%
  clinic_qty: number       // DaySmart
  warehouse_qty: number    // Warehouse
  total_qty: number
  weekly_demand: number
  woc: number | null       // total_qty / weekly_demand
  expiry_date: string | null  // ISO date
  notes: string | null
  status: 'ACTIVE' | 'INACTIVE' | 'DISCONTINUED'
}

export const INVENTORY: InventoryItem[] = [
  // ─── MEDICINE (GP floor 70%) ───────────────────────────────────────────────
  {
    sku_code: '50010352', name: 'Vetoquinol - Aurizon Ear Drops',
    brand: 'Vetoquinol', category: 'Medicine', supplier: 'Alfamedic', uom: 'bottle',
    cost_price: 8.00, selling_price: 36.00, gp_floor: 0.70,
    clinic_qty: 4, warehouse_qty: 0, total_qty: 4, weekly_demand: 5,
    woc: 0.8, expiry_date: '2026-09-15', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50010319', name: 'Entyce - Capromorelin Oral Solution',
    brand: 'Entyce', category: 'Medicine', supplier: 'Alfamedic', uom: 'bottle',
    cost_price: 46.33, selling_price: 198.00, gp_floor: 0.70,
    clinic_qty: 3, warehouse_qty: 1, total_qty: 4, weekly_demand: 2.2,
    woc: 1.8, expiry_date: '2027-03-15', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50010308', name: 'Vetoquinol - Marbocyl P Tablet',
    brand: 'Vetoquinol', category: 'Medicine', supplier: 'Alfamedic', uom: 'tablet',
    cost_price: 5.80, selling_price: 26.00, gp_floor: 0.70,
    clinic_qty: 21, warehouse_qty: 11, total_qty: 32, weekly_demand: 10,
    woc: 3.2, expiry_date: '2027-08-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50010320', name: 'Bova - Gabapentin 25mg Tablet',
    brand: 'Bova', category: 'Medicine', supplier: 'Alfamedic', uom: 'tablet',
    cost_price: 3.92, selling_price: 17.00, gp_floor: 0.70,
    clinic_qty: 100, warehouse_qty: 92, total_qty: 192, weekly_demand: 80,
    woc: 2.4, expiry_date: '2026-08-01', notes: 'Controlled — check stock frequently', status: 'ACTIVE',
  },
  {
    sku_code: '50010326', name: 'Semintra - Telmisartan Oral Solution Cats',
    brand: 'Semintra', category: 'Medicine', supplier: 'Alfamedic', uom: 'bottle',
    cost_price: 435.00, selling_price: 1880.00, gp_floor: 0.70,
    clinic_qty: 2, warehouse_qty: 2, total_qty: 4, weekly_demand: 0.97,
    woc: 4.1, expiry_date: '2027-05-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50010365', name: 'Vetoquinol - Ipakitine 60G',
    brand: 'Vetoquinol', category: 'Medicine', supplier: 'Alfamedic', uom: 'sachet',
    cost_price: 100.00, selling_price: 430.00, gp_floor: 0.70,
    clinic_qty: 4, warehouse_qty: 6, total_qty: 10, weekly_demand: 1.92,
    woc: 5.2, expiry_date: '2027-11-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50010363', name: 'Synco - Phenobarbitone 30mg Tablet',
    brand: 'Synco', category: 'Medicine', supplier: 'Alfamedic', uom: 'tablet',
    cost_price: 0.44, selling_price: 1.90, gp_floor: 0.70,
    clinic_qty: 720, warehouse_qty: 480, total_qty: 1200, weekly_demand: 200,
    woc: 6.0, expiry_date: '2027-12-01', notes: null, status: 'ACTIVE',
  },

  // ─── PREVENTATIVE (GP floor 40%) ───────────────────────────────────────────
  {
    sku_code: '40005812', name: 'Feliway - Refill for Cats',
    brand: 'Feliway', category: 'Preventative', supplier: 'Alfamedic', uom: 'unit',
    cost_price: 227.00, selling_price: 388.00, gp_floor: 0.40,
    clinic_qty: 3, warehouse_qty: 4, total_qty: 7, weekly_demand: 5,
    woc: 1.4, expiry_date: '2027-04-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50010337', name: 'Selehold - Spot On Solution 15mg',
    brand: 'Selehold', category: 'Preventative', supplier: 'Alfamedic', uom: 'pipette',
    cost_price: 182.00, selling_price: 312.00, gp_floor: 0.40,
    clinic_qty: 3, warehouse_qty: 1, total_qty: 4, weekly_demand: 3.3,
    woc: 1.2, expiry_date: '2027-02-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '40005811', name: 'Feliway - Diffuser & Refill for Cats',
    brand: 'Feliway', category: 'Preventative', supplier: 'Alfamedic', uom: 'kit',
    cost_price: 310.00, selling_price: 528.00, gp_floor: 0.40,
    clinic_qty: 2, warehouse_qty: 6, total_qty: 8, weekly_demand: 3.8,
    woc: 2.1, expiry_date: '2027-06-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '40005813', name: 'Feliway - Spray for Cats',
    brand: 'Feliway', category: 'Preventative', supplier: 'Alfamedic', uom: 'bottle',
    cost_price: 220.00, selling_price: 375.00, gp_floor: 0.40,
    clinic_qty: 3, warehouse_qty: 4, total_qty: 7, weekly_demand: 2,
    woc: 3.5, expiry_date: '2027-09-01', notes: null, status: 'ACTIVE',
  },

  // ─── SUPPLEMENT (GP floor 40%) ─────────────────────────────────────────────
  {
    sku_code: '50004544', name: 'VetriScience - Composure PRO Chews 60TABS',
    brand: 'VetriScience', category: 'Supplement', supplier: 'Asia Vet Medical Limited', uom: 'bottle',
    cost_price: 202.00, selling_price: 345.00, gp_floor: 0.40,
    clinic_qty: 3, warehouse_qty: 5, total_qty: 8, weekly_demand: 4.2,
    woc: 1.9, expiry_date: '2027-01-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50009083', name: 'VetPlus - COATEX Skin and Coat Supplement',
    brand: 'VetPlus', category: 'Supplement', supplier: 'Alfamedic', uom: 'capsule',
    cost_price: 115.50, selling_price: 196.00, gp_floor: 0.40,
    clinic_qty: 3, warehouse_qty: 5, total_qty: 8, weekly_demand: 2.86,
    woc: 2.8, expiry_date: '2027-07-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '59999991', name: 'VetriScience - Vetri Lysine Plus Treats Cats',
    brand: 'VetriScience', category: 'Supplement', supplier: 'Asia Vet Medical Limited', uom: 'bottle',
    cost_price: 156.00, selling_price: 265.00, gp_floor: 0.40,
    clinic_qty: 4, warehouse_qty: 8, total_qty: 12, weekly_demand: 3.87,
    woc: 3.1, expiry_date: '2027-10-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50004546', name: 'VetriScience - Glyco Flex II Canine 120TABS',
    brand: 'VetriScience', category: 'Supplement', supplier: 'Asia Vet Medical Limited', uom: 'bottle',
    cost_price: 297.00, selling_price: 505.00, gp_floor: 0.40,
    clinic_qty: 2, warehouse_qty: 6, total_qty: 8, weekly_demand: 3.08,
    woc: 2.6, expiry_date: '2027-11-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50009084', name: 'Vetplus - Samylin Hepatic Supp Large Breed',
    brand: 'VetPlus', category: 'Supplement', supplier: 'Alfamedic', uom: 'sachet',
    cost_price: 730.00, selling_price: 1240.00, gp_floor: 0.40,
    clinic_qty: 1, warehouse_qty: 3, total_qty: 4, weekly_demand: 0.93,
    woc: 4.3, expiry_date: '2027-12-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50004554', name: 'VetriScience - Omega 3,6,9 PRO 30CAPS',
    brand: 'VetriScience', category: 'Supplement', supplier: 'Asia Vet Medical Limited', uom: 'bottle',
    cost_price: 70.00, selling_price: 119.00, gp_floor: 0.40,
    clinic_qty: 0, warehouse_qty: 0, total_qty: 0, weekly_demand: 2,
    woc: null, expiry_date: null, notes: 'New SKU — not yet stocked', status: 'ACTIVE',
  },
  {
    sku_code: '50010024', name: 'Blue Pet Co - GoActive Joint Chicken 30g',
    brand: 'Blue Pet Co', category: 'Supplement', supplier: 'Blue Pet Co', uom: 'bag',
    cost_price: 0.00, selling_price: 0.00, gp_floor: 0.40,
    clinic_qty: 0, warehouse_qty: 0, total_qty: 0, weekly_demand: 1,
    woc: null, expiry_date: null, notes: 'Cost TBC — awaiting supplier quote', status: 'ACTIVE',
  },

  // ─── FOOD (GP floor 35%) ───────────────────────────────────────────────────
  {
    sku_code: '10004975', name: 'Almo Nature - Wet Food Cats Daily Menu 85G',
    brand: 'Almo Nature', category: 'Food', supplier: "Arrowana Int'l Ltd", uom: 'can',
    cost_price: 7.50, selling_price: 11.60, gp_floor: 0.35,
    clinic_qty: 48, warehouse_qty: 400, total_qty: 448, weekly_demand: 30,
    woc: 14.9, expiry_date: '2026-11-01', notes: 'Min. order 24 cans', status: 'ACTIVE',
  },
  {
    sku_code: '10004964', name: 'Almo Nature - Dry Food Sterilised Cats 2KG',
    brand: 'Almo Nature', category: 'Food', supplier: "Arrowana Int'l Ltd", uom: 'bag',
    cost_price: 168.00, selling_price: 260.00, gp_floor: 0.35,
    clinic_qty: 6, warehouse_qty: 24, total_qty: 30, weekly_demand: 4,
    woc: 7.5, expiry_date: '2027-01-15', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '10004928', name: 'Almo Nature - Dry Food Medium/Large Dogs',
    brand: 'Almo Nature', category: 'Food', supplier: "Arrowana Int'l Ltd", uom: 'bag',
    cost_price: 518.00, selling_price: 798.00, gp_floor: 0.35,
    clinic_qty: 2, warehouse_qty: 6, total_qty: 8, weekly_demand: 2.1,
    woc: 3.8, expiry_date: '2027-02-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '10005048', name: 'Almo Nature - Wet Food Dogs HFC Beef 290G',
    brand: 'Almo Nature', category: 'Food', supplier: "Arrowana Int'l Ltd", uom: 'can',
    cost_price: 26.00, selling_price: 40.00, gp_floor: 0.35,
    clinic_qty: 12, warehouse_qty: 60, total_qty: 72, weekly_demand: 4.5,
    woc: 16.0, expiry_date: '2026-12-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '10005059', name: 'Almo Nature - Wet Food Dogs Chicken Carrots',
    brand: 'Almo Nature', category: 'Food', supplier: "Arrowana Int'l Ltd", uom: 'can',
    cost_price: 26.00, selling_price: 40.00, gp_floor: 0.35,
    clinic_qty: 6, warehouse_qty: 14, total_qty: 20, weekly_demand: 5,
    woc: 4.0, expiry_date: '2026-12-01', notes: null, status: 'ACTIVE',
  },

  // ─── PET HYGIENE (GP floor 40%) ────────────────────────────────────────────
  {
    sku_code: '50007726', name: 'Oxyfresh - Oral Hygiene Solution 473ML',
    brand: 'Oxyfresh', category: 'Pet Hygiene', supplier: 'Asia Vet Medical Limited', uom: 'bottle',
    cost_price: 102.00, selling_price: 172.00, gp_floor: 0.40,
    clinic_qty: 5, warehouse_qty: 17, total_qty: 22, weekly_demand: 8.1,
    woc: 2.7, expiry_date: '2027-07-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '50010346', name: 'Vetoquinol - Ear Cleansing Solution',
    brand: 'Vetoquinol', category: 'Pet Hygiene', supplier: 'Alfamedic', uom: 'bottle',
    cost_price: 73.00, selling_price: 124.00, gp_floor: 0.40,
    clinic_qty: 4, warehouse_qty: 10, total_qty: 14, weekly_demand: 3.1,
    woc: 4.5, expiry_date: '2027-09-01', notes: null, status: 'ACTIVE',
  },
  {
    sku_code: '59999992', name: 'Dechra - Lubrithal Eye Gel for Cats & Dogs',
    brand: 'Dechra', category: 'Pet Hygiene', supplier: 'Alfamedic', uom: 'tube',
    cost_price: 112.00, selling_price: 190.00, gp_floor: 0.40,
    clinic_qty: 4, warehouse_qty: 9, total_qty: 13, weekly_demand: 3.94,
    woc: 3.3, expiry_date: '2027-10-01', notes: null, status: 'ACTIVE',
  },
]
